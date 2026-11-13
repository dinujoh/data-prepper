/*
 * Copyright OpenSearch Contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package org.opensearch.dataprepper.plugins.source.otellogs;

import static org.hamcrest.MatcherAssert.assertThat;
import static org.hamcrest.Matchers.equalTo;
import static org.hamcrest.Matchers.hasItem;
import static org.hamcrest.Matchers.not;
import static org.hamcrest.Matchers.startsWith;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.opensearch.dataprepper.plugins.source.otellogs.OTelLogsSourceConfig.DEFAULT_PORT;
import static org.opensearch.dataprepper.plugins.source.otellogs.OTelLogsSourceConfig.DEFAULT_REQUEST_TIMEOUT_MS;
import static org.opensearch.dataprepper.plugins.source.otellogs.OtelLogsSourceConfigTestData.CONFIG_GRPC_PATH;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Optional;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.opensearch.dataprepper.armeria.authentication.GrpcAuthenticationProvider;
import org.opensearch.dataprepper.metrics.PluginMetrics;
import org.opensearch.dataprepper.model.buffer.Buffer;
import org.opensearch.dataprepper.model.buffer.SizeOverflowException;
import org.opensearch.dataprepper.model.configuration.PipelineDescription;
import org.opensearch.dataprepper.model.configuration.PluginSetting;
import org.opensearch.dataprepper.model.plugin.PluginFactory;
import org.opensearch.dataprepper.model.record.Record;
import org.opensearch.dataprepper.plugins.GrpcBasicAuthenticationProvider;
import org.opensearch.dataprepper.plugins.codec.CompressionOption;
import org.opensearch.dataprepper.plugins.server.RetryInfoConfig;

import io.opentelemetry.proto.collector.logs.v1.ExportLogsServiceRequest;
import io.opentelemetry.proto.logs.v1.LogRecord;
import io.opentelemetry.proto.logs.v1.ResourceLogs;
import io.opentelemetry.proto.logs.v1.ScopeLogs;

@ExtendWith(MockitoExtension.class)
class OtelLogsSource_CloseConnectionOnThrottleTest {
    private static final String TEST_PIPELINE_NAME = "test_pipeline";

    @Mock
    private PluginFactory pluginFactory;

    @Mock
    private GrpcBasicAuthenticationProvider authenticationProvider;

    @Mock(lenient = true)
    private OTelLogsSourceConfig oTelLogsSourceConfig;

    @Mock
    private Buffer<Record<Object>> buffer;

    private OTelLogsSource source;

    private void startSource(final boolean closeConnectionOnThrottle) throws Exception {
        lenient().when(authenticationProvider.getHttpAuthenticationService()).thenReturn(Optional.empty());
        lenient().doThrow(SizeOverflowException.class).when(buffer).writeAll(any(), anyInt());

        when(oTelLogsSourceConfig.getPort()).thenReturn(DEFAULT_PORT);
        when(oTelLogsSourceConfig.isSsl()).thenReturn(false);
        when(oTelLogsSourceConfig.getRequestTimeoutInMillis()).thenReturn(DEFAULT_REQUEST_TIMEOUT_MS);
        when(oTelLogsSourceConfig.getMaxConnectionCount()).thenReturn(10);
        when(oTelLogsSourceConfig.getThreadCount()).thenReturn(5);
        when(oTelLogsSourceConfig.getCompression()).thenReturn(CompressionOption.NONE);
        when(oTelLogsSourceConfig.getRetryInfo()).thenReturn(new RetryInfoConfig(Duration.ofMillis(100), Duration.ofMillis(2000)));
        when(oTelLogsSourceConfig.getPath()).thenReturn(CONFIG_GRPC_PATH);
        when(oTelLogsSourceConfig.enableUnframedRequests()).thenReturn(true);
        when(oTelLogsSourceConfig.closeConnectionOnThrottle()).thenReturn(closeConnectionOnThrottle);

        when(pluginFactory.loadPlugin(eq(GrpcAuthenticationProvider.class), any(PluginSetting.class)))
                .thenReturn(authenticationProvider);

        final PipelineDescription pipelineDescription = mock(PipelineDescription.class);
        lenient().when(pipelineDescription.getPipelineName()).thenReturn(TEST_PIPELINE_NAME);
        source = new OTelLogsSource(oTelLogsSourceConfig, PluginMetrics.fromNames("otel_logs", "pipeline"),
                pluginFactory, pipelineDescription);
        source.start(buffer);
    }

    @AfterEach
    void afterEach() {
        if (source != null) {
            source.stop();
        }
    }

    @Test
    void throttled_unframed_request_gets_connection_close_when_enabled() throws Exception {
        startSource(true);

        final List<String> response = sendUnframedHttp11Request();

        assertThat(response.get(0), startsWith("HTTP/1.1 429"));
        assertThat(response, hasItem("connection: close"));
    }

    @Test
    void throttled_unframed_request_keeps_connection_open_by_default() throws Exception {
        startSource(false);

        final List<String> response = sendUnframedHttp11Request();

        assertThat(response.get(0), startsWith("HTTP/1.1 429"));
        assertThat(response, not(hasItem("connection: close")));
    }

    /**
     * Sends one HTTP/1.1 keep-alive request over a raw socket and returns the status line and the
     * header lines, lower-cased, so the test sees exactly what a client such as the OpenTelemetry
     * Collector would receive.
     */
    private static List<String> sendUnframedHttp11Request() throws Exception {
        final byte[] body = ExportLogsServiceRequest.newBuilder()
                .addResourceLogs(ResourceLogs.newBuilder()
                        .addScopeLogs(ScopeLogs.newBuilder()
                                .addLogRecords(LogRecord.newBuilder().setSeverityNumberValue(1))))
                .build()
                .toByteArray();
        try (Socket socket = new Socket("127.0.0.1", DEFAULT_PORT)) {
            socket.setSoTimeout(10_000);
            final OutputStream out = socket.getOutputStream();
            out.write(("POST " + CONFIG_GRPC_PATH + " HTTP/1.1\r\n"
                    + "Host: 127.0.0.1:" + DEFAULT_PORT + "\r\n"
                    + "Content-Type: application/x-protobuf\r\n"
                    + "Content-Length: " + body.length + "\r\n"
                    + "Connection: keep-alive\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
            out.write(body);
            out.flush();

            final BufferedReader reader = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.US_ASCII));
            final List<String> lines = new ArrayList<>();
            String line;
            while ((line = reader.readLine()) != null && !line.isEmpty()) {
                lines.add(lines.isEmpty() ? line : line.toLowerCase(Locale.ROOT));
            }
            return lines;
        }
    }
}
