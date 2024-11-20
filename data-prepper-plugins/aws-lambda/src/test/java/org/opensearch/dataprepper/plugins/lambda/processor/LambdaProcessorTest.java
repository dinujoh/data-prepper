/*
 * Copyright OpenSearch Contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package org.opensearch.dataprepper.plugins.lambda.processor;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.DistributionSummary;
import io.micrometer.core.instrument.Timer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.opensearch.dataprepper.aws.api.AwsCredentialsSupplier;
import org.opensearch.dataprepper.expression.ExpressionEvaluator;
import org.opensearch.dataprepper.model.acknowledgements.AcknowledgementSet;
import org.opensearch.dataprepper.model.codec.InputCodec;
import org.opensearch.dataprepper.model.configuration.PluginSetting;
import org.opensearch.dataprepper.model.event.DefaultEventHandle;
import org.opensearch.dataprepper.model.event.Event;
import org.opensearch.dataprepper.model.event.EventMetadata;
import org.opensearch.dataprepper.model.plugin.PluginFactory;
import org.opensearch.dataprepper.model.record.Record;
import org.opensearch.dataprepper.plugins.codec.json.JsonInputCodec;
import org.opensearch.dataprepper.plugins.codec.json.JsonInputCodecConfig;
import org.opensearch.dataprepper.plugins.lambda.common.accumlator.Buffer;
import org.opensearch.dataprepper.plugins.lambda.common.accumlator.InMemoryBuffer;
import org.opensearch.dataprepper.plugins.lambda.common.config.AwsAuthenticationOptions;
import org.opensearch.dataprepper.plugins.lambda.common.config.BatchOptions;
import org.opensearch.dataprepper.plugins.lambda.common.config.ClientOptions;
import org.opensearch.dataprepper.plugins.lambda.common.config.InvocationType;
import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.lambda.LambdaAsyncClient;
import software.amazon.awssdk.services.lambda.model.InvokeRequest;
import software.amazon.awssdk.services.lambda.model.InvokeResponse;

import java.io.InputStream;
import java.lang.reflect.Field;
import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.params.provider.Arguments.arguments;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyDouble;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.opensearch.dataprepper.plugins.lambda.utils.LambdaTestSetupUtil.createLambdaConfigurationFromYaml;
import static org.opensearch.dataprepper.plugins.lambda.utils.LambdaTestSetupUtil.getSampleEventRecords;
import static org.opensearch.dataprepper.plugins.lambda.utils.LambdaTestSetupUtil.getSampleRecord;

@MockitoSettings(strictness = Strictness.LENIENT)
public class LambdaProcessorTest {

    // Mock dependencies
    @Mock
    private AwsAuthenticationOptions awsAuthenticationOptions;

    @Mock
    private Buffer bufferMock;

    @Mock
    private PluginFactory pluginFactory;

    @Mock
    private PluginSetting pluginSetting;

    @Mock
    private AwsCredentialsSupplier awsCredentialsSupplier;

    @Mock
    private ExpressionEvaluator expressionEvaluator;

    @Mock
    private InputCodec responseCodec;


    @Mock
    private Counter numberOfRecordsSuccessCounter;

    @Mock
    private Counter numberOfRequestsSuccessCounter;

    @Mock
    private Counter numberOfRecordsFailedCounter;

    @Mock
    private Counter numberOfRequestsFailedCounter;

    @Mock
    private DistributionSummary requestPayloadMetric;

    @Mock
    private DistributionSummary responsePayloadMetric;

    @Mock
    private InvokeResponse invokeResponse;

    @Mock
    private Timer lambdaLatencyMetric;

    @Mock
    private LambdaAsyncClient lambdaAsyncClient;


    private static Stream<Arguments> getLambdaResponseConversionSamples() {
        return Stream.of(
                arguments("lambda-processor-success-config.yaml", null),
                arguments("lambda-processor-success-config.yaml", SdkBytes.fromByteArray("{}".getBytes())),
                arguments("lambda-processor-success-config.yaml", SdkBytes.fromByteArray("[]".getBytes()))
        );
    }

    @BeforeEach
    public void setUp() throws Exception {
        MockitoAnnotations.openMocks(this);

        when(pluginSetting.getName()).thenReturn("testProcessor");
        when(pluginSetting.getPipelineName()).thenReturn("testPipeline");
/*
        // Mock PluginMetrics counters and timers
        when(pluginMetrics.counter(eq(NUMBER_OF_RECORDS_FLUSHED_TO_LAMBDA_SUCCESS))).thenReturn(
            numberOfRecordsSuccessCounter);
        when(pluginMetrics.counter(eq(NUMBER_OF_RECORDS_FLUSHED_TO_LAMBDA_FAILED))).thenReturn(
            numberOfRecordsFailedCounter);
        when(pluginMetrics.counter(eq(NUMBER_OF_SUCCESSFUL_REQUESTS_TO_LAMBDA))).thenReturn(
            numberOfRecordsSuccessCounter);
        when(pluginMetrics.counter(eq(NUMBER_OF_FAILED_REQUESTS_TO_LAMBDA))).thenReturn(
            numberOfRecordsFailedCounter);
        when(pluginMetrics.timer(anyString())).thenReturn(lambdaLatencyMetric);
*/


        // Mock AWS Authentication Options
        when(awsAuthenticationOptions.getAwsRegion()).thenReturn(Region.US_EAST_1);
        when(awsAuthenticationOptions.getAwsStsRoleArn()).thenReturn("testRole");

        // Mock BatchOptions and ThresholdOptions

        // Mock PluginFactory to return the mocked responseCodec
        when(pluginFactory.loadPlugin(eq(InputCodec.class), any(PluginSetting.class))).thenReturn(
                new JsonInputCodec(new JsonInputCodecConfig()));

        // Instantiate the LambdaProcessor manually


//        populatePrivateFields();
        //setPrivateField(lambdaProcessor, "pluginMetrics", pluginMetrics);
        // Mock InvokeResponse
        when(invokeResponse.payload()).thenReturn(SdkBytes.fromUtf8String("[{\"key\":\"value\"}]"));
        when(invokeResponse.statusCode()).thenReturn(200); // Success status code

        // Mock the invoke method to return a completed future
        CompletableFuture<InvokeResponse> invokeFuture = CompletableFuture.completedFuture(
                invokeResponse);
        when(lambdaAsyncClient.invoke(any(InvokeRequest.class))).thenReturn(invokeFuture);

        // Mock Response Codec parse method
//        doNothing().when(responseCodec).parse(any(InputStream.class), any(Consumer.class));

    }

    private void populatePrivateFields(LambdaProcessor lambdaProcessor) throws Exception {
        // Use reflection to set the private fields
        setPrivateField(lambdaProcessor, "numberOfRecordsSuccessCounter",
                numberOfRecordsSuccessCounter);
        setPrivateField(lambdaProcessor, "numberOfRequestsSuccessCounter",
                numberOfRequestsSuccessCounter);
        setPrivateField(lambdaProcessor, "numberOfRecordsFailedCounter",
                numberOfRecordsFailedCounter);
        setPrivateField(lambdaProcessor, "numberOfRequestsFailedCounter",
                numberOfRequestsFailedCounter);
        setPrivateField(lambdaProcessor, "lambdaLatencyMetric", lambdaLatencyMetric);
        setPrivateField(lambdaProcessor, "responsePayloadMetric", responsePayloadMetric);
        setPrivateField(lambdaProcessor, "requestPayloadMetric", requestPayloadMetric);
        setPrivateField(lambdaProcessor, "lambdaAsyncClient", lambdaAsyncClient);
    }

    // Helper method to set private fields via reflection
    private void setPrivateField(Object targetObject, String fieldName, Object value)
            throws Exception {
        Field field = targetObject.getClass().getDeclaredField(fieldName);
        field.setAccessible(true);
        field.set(targetObject, value);
    }

    @Test
    public void testProcessorDefaults() {
        // Create a new LambdaProcessorConfig with default values
        LambdaProcessorConfig defaultConfig = new LambdaProcessorConfig();

        // Test default values
        assertNull(defaultConfig.getFunctionName());
        assertNull(defaultConfig.getAwsAuthenticationOptions());
        assertNull(defaultConfig.getResponseCodecConfig());
        assertEquals(InvocationType.REQUEST_RESPONSE, defaultConfig.getInvocationType());
        assertFalse(defaultConfig.getResponseEventsMatch());
        assertNull(defaultConfig.getWhenCondition());
        assertTrue(defaultConfig.getTagsOnFailure().isEmpty());

        // Test ClientOptions defaults
        ClientOptions clientOptions = defaultConfig.getClientOptions();
        assertNotNull(clientOptions);
        assertEquals(ClientOptions.DEFAULT_CONNECTION_RETRIES,
                clientOptions.getMaxConnectionRetries());
        assertEquals(ClientOptions.DEFAULT_API_TIMEOUT, clientOptions.getApiCallTimeout());
        assertEquals(ClientOptions.DEFAULT_CONNECTION_TIMEOUT,
                clientOptions.getConnectionTimeout());
        assertEquals(ClientOptions.DEFAULT_MAXIMUM_CONCURRENCY, clientOptions.getMaxConcurrency());
        assertEquals(ClientOptions.DEFAULT_BASE_DELAY, clientOptions.getBaseDelay());
        assertEquals(ClientOptions.DEFAULT_MAX_BACKOFF, clientOptions.getMaxBackoff());

        // Test BatchOptions defaults
        BatchOptions batchOptions = defaultConfig.getBatchOptions();
        assertNotNull(batchOptions);
    }

    @ParameterizedTest
    @ValueSource(strings = {"lambda-processor-success-config.yaml"})
    public void testDoExecute_WithExceptionInSendRecords(String configFileName) throws Exception {
        // Arrange
        List<Record<Event>> records = Collections.singletonList(getSampleRecord());
        LambdaProcessorConfig lambdaProcessorConfig = createLambdaConfigurationFromYaml(
                configFileName);
        LambdaProcessor lambdaProcessor = new LambdaProcessor(pluginFactory, pluginSetting, lambdaProcessorConfig,
                awsCredentialsSupplier, expressionEvaluator);
        populatePrivateFields(lambdaProcessor);


        when(lambdaAsyncClient.invoke(any(InvokeRequest.class))).thenThrow(new RuntimeException("test exception"));
        Collection<Record<Event>> outputRecords = lambdaProcessor.doExecute(records);
        assertNotNull(outputRecords);
        assertEquals(1, outputRecords.size());
        Record<Event> record = outputRecords.iterator().next();
        assertEquals("[lambda_failure]", record.getData().getMetadata().getTags().toString());

    }

    @ParameterizedTest
    @ValueSource(strings = {"lambda-processor-success-config.yaml"})
    public void testDoExecute_WithExceptionDuringProcessing(String configFileName) throws Exception {
        // Arrange
        List<Record<Event>> records = Collections.singletonList(getSampleRecord());
        LambdaProcessorConfig lambdaProcessorConfig = createLambdaConfigurationFromYaml(
                configFileName);
        LambdaProcessor lambdaProcessor = new LambdaProcessor(pluginFactory, pluginSetting, lambdaProcessorConfig,
                awsCredentialsSupplier, expressionEvaluator);
        populatePrivateFields(lambdaProcessor);


        CompletableFuture<InvokeResponse> invokeFuture = CompletableFuture.completedFuture(
                invokeResponse);
        when(lambdaAsyncClient.invoke(any(InvokeRequest.class))).thenReturn(invokeFuture);
        when(invokeResponse.payload()).thenThrow(new RuntimeException("Test Exception"));


        Collection<Record<Event>> result = lambdaProcessor.doExecute(records);
        // Assert
        assertEquals(1, result.size());
        verify(numberOfRecordsFailedCounter, times(1)).increment(1.0);
    }

    @ParameterizedTest
    @ValueSource(strings = {"lambda-processor-success-config.yaml"})
    public void testDoExecute_UnableParseResponse(String configFileName) throws Exception {
        // Arrange
        int recordCount = (int) (Math.random() * 100);
        List<Record<Event>> records = getSampleEventRecords(recordCount);
        InvokeResponse invokeResponse = mock(InvokeResponse.class);


        // Mock Buffer to return empty payload
        when(invokeResponse.payload()).thenReturn(SdkBytes.fromUtf8String("[{\"key\": \"value\"}]"));
        LambdaProcessorConfig lambdaProcessorConfig = createLambdaConfigurationFromYaml(configFileName);
        LambdaProcessor lambdaProcessor = new LambdaProcessor(pluginFactory, pluginSetting, lambdaProcessorConfig,
                awsCredentialsSupplier, expressionEvaluator);
        populatePrivateFields(lambdaProcessor);
        // Act
        Collection<Record<Event>> result = lambdaProcessor.doExecute(records);

        // Assert
        assertEquals(recordCount, result.size(), "Result should be empty due to empty Lambda response.");
        verify(numberOfRecordsSuccessCounter, times(0)).increment(1.0);
        verify(numberOfRecordsFailedCounter, times(1)).increment(recordCount);
    }

    @ParameterizedTest
    @ValueSource(strings = {"lambda-processor-success-config.yaml"})
    public void testDoExecute_WithNullResponse_get_original_records_with_tags(String configFileName) throws Exception {
        // Arrange

        List<Record<Event>> records = getSampleEventRecords(1);

        // Mock Buffer to return null payload
        when(invokeResponse.payload()).thenReturn(null);
        LambdaProcessorConfig lambdaProcessorConfig = createLambdaConfigurationFromYaml(configFileName);
        LambdaProcessor lambdaProcessor = new LambdaProcessor(pluginFactory, pluginSetting, lambdaProcessorConfig,
                awsCredentialsSupplier, expressionEvaluator);
        populatePrivateFields(lambdaProcessor);
        // Act
        Collection<Record<Event>> result = lambdaProcessor.doExecute(records);

        // Assert
        assertEquals(1, result.size(), "Result should be empty due to null Lambda response.");
        for (Record<Event> record : result) {
            EventMetadata metadata = record.getData().getMetadata();
            assertEquals(1, metadata.getTags().size());
            assertEquals("[lambda_failure]", metadata.getTags().toString());
        }
        verify(numberOfRecordsFailedCounter, times(1)).increment(1.0);
        verify(numberOfRecordsSuccessCounter, times(0)).increment(0);
    }

    @ParameterizedTest
    @ValueSource(strings = {"lambda-processor-success-config.yaml"})
    public void testDoExecute_WithEmptyRecords(String configFileName) {
        // Arrange
        Collection<Record<Event>> records = Collections.emptyList();

        // Act
        LambdaProcessorConfig lambdaProcessorConfig = createLambdaConfigurationFromYaml(configFileName);
        LambdaProcessor lambdaProcessor = new LambdaProcessor(pluginFactory, pluginSetting, lambdaProcessorConfig,
                awsCredentialsSupplier, expressionEvaluator);
        Collection<Record<Event>> result = lambdaProcessor.doExecute(records);

        // Assert
        assertEquals(0, result.size(), "Result should be empty when input records are empty.");
        verify(numberOfRecordsSuccessCounter, never()).increment(anyDouble());
        verify(numberOfRecordsFailedCounter, never()).increment(anyDouble());
    }

    @ParameterizedTest
    @ValueSource(strings = {"lambda-processor-when-condition-config.yaml"})
    public void testDoExecute_WhenConditionFalse(String configFileName) {
        // Arrange
        Event event = mock(Event.class);
        DefaultEventHandle eventHandle = mock(DefaultEventHandle.class);
        AcknowledgementSet acknowledgementSet = mock(AcknowledgementSet.class);
        when(event.getEventHandle()).thenReturn(eventHandle);
        when(eventHandle.getAcknowledgementSet()).thenReturn(acknowledgementSet);
        Record<Event> record = new Record<>(event);
        Collection<Record<Event>> records = Collections.singletonList(record);

        // Instantiate the LambdaProcessor manually
        LambdaProcessorConfig lambdaProcessorConfig = createLambdaConfigurationFromYaml(configFileName);
        LambdaProcessor lambdaProcessor = new LambdaProcessor(pluginFactory, pluginSetting, lambdaProcessorConfig,
                awsCredentialsSupplier, expressionEvaluator);
        // Mock condition evaluator to return false
        when(expressionEvaluator.evaluateConditional(anyString(), eq(event))).thenReturn(false);

        // Act
        Collection<Record<Event>> result = lambdaProcessor.doExecute(records);

        // Assert
        assertEquals(1, result.size(),
                "Result should contain one record as the condition is false.");
        verify(numberOfRecordsSuccessCounter, never()).increment(anyDouble());
        verify(numberOfRecordsFailedCounter, never()).increment(anyDouble());
    }

    @ParameterizedTest
    @ValueSource(strings = {"lambda-processor-success-config.yaml"})
    public void testDoExecute_SuccessfulProcessing(String configFileName) throws Exception {
        // Arrange
        int recordCount = 1;
        Collection<Record<Event>> records = getSampleEventRecords(recordCount);

        // Mock the invoke method to return a completed future
        InvokeResponse invokeResponse = InvokeResponse.builder()
                .payload(SdkBytes.fromUtf8String("[{\"key1\": \"value1\", \"key2\": \"value2\"}]"))
                .statusCode(200)
                .build();
        CompletableFuture<InvokeResponse> invokeFuture = CompletableFuture.completedFuture(invokeResponse);
        when(lambdaAsyncClient.invoke(any(InvokeRequest.class))).thenReturn(invokeFuture);


        // Act
        // Instantiate the LambdaProcessor manually
        LambdaProcessorConfig lambdaProcessorConfig = createLambdaConfigurationFromYaml(configFileName);
        LambdaProcessor lambdaProcessor = new LambdaProcessor(pluginFactory, pluginSetting, lambdaProcessorConfig,
                awsCredentialsSupplier, expressionEvaluator);
        populatePrivateFields(lambdaProcessor);
        Collection<Record<Event>> result = lambdaProcessor.doExecute(records);

        // Assert
        assertEquals(recordCount, result.size(), "Result should contain one record.");
        verify(numberOfRecordsSuccessCounter, times(1)).increment(1.0);
    }

    @ParameterizedTest
    @ValueSource(strings = {"lambda-processor-success-config.yaml"})
    public void testConvertLambdaResponseToEvent_WithEqualEventCounts_SuccessfulProcessing(String configFileName)
            throws Exception {
        // Arrange


        // Mock LambdaResponse with a valid payload containing two events
        String payloadString = "[{\"key\":\"value1\"}, {\"key\":\"value2\"}]";
        SdkBytes sdkBytes = SdkBytes.fromByteArray(payloadString.getBytes());
        when(invokeResponse.payload()).thenReturn(sdkBytes);
        when(invokeResponse.statusCode()).thenReturn(200); // Success status code

        // Mock the responseCodec.parse to add two events
        doAnswer(invocation -> {
            invocation.getArgument(0);
            @SuppressWarnings("unchecked")
            Consumer<Record<Event>> consumer = invocation.getArgument(1);
            Event parsedEvent1 = mock(Event.class);
            Event parsedEvent2 = mock(Event.class);
            consumer.accept(new Record<>(parsedEvent1));
            consumer.accept(new Record<>(parsedEvent2));
            return null;
        }).when(responseCodec).parse(any(InputStream.class), any(Consumer.class));

        // Mock buffer with two original events
        Event originalEvent1 = mock(Event.class);
        Event originalEvent2 = mock(Event.class);
        DefaultEventHandle eventHandle = mock(DefaultEventHandle.class);
        AcknowledgementSet acknowledgementSet = mock(AcknowledgementSet.class);
        when(eventHandle.getAcknowledgementSet()).thenReturn(acknowledgementSet);

        when(originalEvent1.getEventHandle()).thenReturn(eventHandle);
        when(originalEvent2.getEventHandle()).thenReturn(eventHandle);
        Record<Event> originalRecord1 = new Record<>(originalEvent1);
        Record<Event> originalRecord2 = new Record<>(originalEvent2);
        List<Record<Event>> originalRecords = Arrays.asList(originalRecord1, originalRecord2);
        when(bufferMock.getRecords()).thenReturn(originalRecords);
        when(bufferMock.getEventCount()).thenReturn(2);

        // Act
        LambdaProcessorConfig lambdaProcessorConfig = createLambdaConfigurationFromYaml(configFileName);
        LambdaProcessor lambdaProcessor = new LambdaProcessor(pluginFactory, pluginSetting, lambdaProcessorConfig,
                awsCredentialsSupplier, expressionEvaluator);
        List<Record<Event>> resultRecords = lambdaProcessor.convertLambdaResponseToEvent(bufferMock,
                invokeResponse);

        // Assert
        assertEquals(2, resultRecords.size(), "ResultRecords should contain two records.");
        // Verify that failure tags are not added since it's a successful response
        verify(originalEvent1, never()).getMetadata();
        verify(originalEvent2, never()).getMetadata();
    }

    @ParameterizedTest
    @ValueSource(strings = {"lambda-processor-unequal-success-config.yaml"})
    public void testConvertLambdaResponseToEvent_WithUnequalEventCounts_SuccessfulProcessing(String configFileName)
            throws Exception {
        // Arrange
        // Set responseEventsMatch to false


        // Mock LambdaResponse with a valid payload containing three events
        String payloadString = "[{\"key\":\"value1\"}, {\"key\":\"value2\"}, {\"key\":\"value3\"}]";
        SdkBytes sdkBytes = SdkBytes.fromByteArray(payloadString.getBytes());
        when(invokeResponse.payload()).thenReturn(sdkBytes);
        when(invokeResponse.statusCode()).thenReturn(200); // Success status code

        // Mock the responseCodec.parse to add three parsed events
        doAnswer(invocation -> {
            invocation.getArgument(0);
            @SuppressWarnings("unchecked")
            Consumer<Record<Event>> consumer = invocation.getArgument(1);

            // Create and add three mocked parsed events
            Event parsedEvent1 = mock(Event.class);
            Event parsedEvent2 = mock(Event.class);
            Event parsedEvent3 = mock(Event.class);
            consumer.accept(new Record<>(parsedEvent1));
            consumer.accept(new Record<>(parsedEvent2));
            consumer.accept(new Record<>(parsedEvent3));

            return null;
        }).when(responseCodec).parse(any(InputStream.class), any(Consumer.class));

        // Mock buffer with two original events
        Event originalEvent1 = mock(Event.class);
        EventMetadata originalMetadata1 = mock(EventMetadata.class);
        when(originalEvent1.getMetadata()).thenReturn(originalMetadata1);

        Event originalEvent2 = mock(Event.class);
        EventMetadata originalMetadata2 = mock(EventMetadata.class);
        when(originalEvent2.getMetadata()).thenReturn(originalMetadata2);

        DefaultEventHandle eventHandle = mock(DefaultEventHandle.class);
        AcknowledgementSet acknowledgementSet = mock(AcknowledgementSet.class);
        when(eventHandle.getAcknowledgementSet()).thenReturn(acknowledgementSet);

        when(originalEvent1.getEventHandle()).thenReturn(eventHandle);
        when(originalEvent2.getEventHandle()).thenReturn(eventHandle);

        Record<Event> originalRecord1 = new Record<>(originalEvent1);
        Record<Event> originalRecord2 = new Record<>(originalEvent2);
        List<Record<Event>> originalRecords = Arrays.asList(originalRecord1, originalRecord2);
        when(bufferMock.getRecords()).thenReturn(originalRecords);
        when(bufferMock.getEventCount()).thenReturn(2);

        // Act
        LambdaProcessorConfig lambdaProcessorConfig = createLambdaConfigurationFromYaml(configFileName);
        LambdaProcessor lambdaProcessor = new LambdaProcessor(pluginFactory, pluginSetting, lambdaProcessorConfig,
                awsCredentialsSupplier, expressionEvaluator);
        List<Record<Event>> resultRecords = lambdaProcessor.convertLambdaResponseToEvent(bufferMock, invokeResponse);
        // Assert
        // Verify that three records are added to the result
        assertEquals(3, resultRecords.size(), "ResultRecords should contain three records.");
    }

    @ParameterizedTest
    @MethodSource("getLambdaResponseConversionSamples")
    public void testConvertLambdaResponseToEvent_ExpectException_when_request_response_do_not_match(String configFile, SdkBytes lambdaReponse) {
        // Arrange
        // Set responseEventsMatch to false
        LambdaProcessorConfig lambdaProcessorConfig = createLambdaConfigurationFromYaml(configFile);
        LambdaProcessor localLambdaProcessor = new LambdaProcessor(pluginFactory, pluginSetting,
                lambdaProcessorConfig, awsCredentialsSupplier, expressionEvaluator);
        InvokeResponse invokeResponse = mock(InvokeResponse.class);
        // Mock LambdaResponse with a valid payload containing three events
        when(invokeResponse.payload()).thenReturn(lambdaReponse);
        when(invokeResponse.statusCode()).thenReturn(200); // Success status code

        int randomCount = (int) (Math.random() * 10);
        List<Record<Event>> originalRecords = getSampleEventRecords(randomCount);
        Buffer buffer = new InMemoryBuffer(lambdaProcessorConfig.getBatchOptions().getKeyName());
        for (Record<Event> originalRecord : originalRecords) {
            buffer.addRecord(originalRecord);
        }
        // Act
        assertThrows(RuntimeException.class, () -> localLambdaProcessor.convertLambdaResponseToEvent(buffer, invokeResponse),
                "For Strict mode request and response size from lambda should match");

    }

}
