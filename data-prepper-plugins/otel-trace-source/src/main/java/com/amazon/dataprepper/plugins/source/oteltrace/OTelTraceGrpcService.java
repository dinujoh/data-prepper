package com.amazon.dataprepper.plugins.source.oteltrace;

import com.amazon.dataprepper.metrics.PluginMetrics;
import com.amazon.dataprepper.model.buffer.Buffer;
import com.amazon.dataprepper.model.record.Record;
import io.grpc.Status;
import io.grpc.stub.StreamObserver;
import io.micrometer.core.instrument.Counter;
import io.opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest;
import io.opentelemetry.proto.collector.trace.v1.ExportTraceServiceResponse;
import io.opentelemetry.proto.collector.trace.v1.TraceServiceGrpc;

import java.util.concurrent.TimeoutException;

public class OTelTraceGrpcService extends TraceServiceGrpc.TraceServiceImplBase {

    public static final String BUFFER_SIZE = "bufferSize";
    public static final String REQUEST_TIMEOUTS = "requestTimeouts";
    public static final String RECORDS_WRITTEN = "recordsWritten";

    private final int bufferWriteTimeoutInMillis;
    private final Buffer<Record<ExportTraceServiceRequest>> buffer;

    private final PluginMetrics pluginMetrics;
    private final Counter requestTimeoutCounter;
    private final Counter recordsWrittenCounter;


    public OTelTraceGrpcService(int bufferWriteTimeoutInMillis,
                                Buffer<Record<ExportTraceServiceRequest>> buffer,
                                final PluginMetrics pluginMetrics) {
        this.bufferWriteTimeoutInMillis = bufferWriteTimeoutInMillis;
        this.buffer = buffer;
        this.pluginMetrics = pluginMetrics;

        requestTimeoutCounter = pluginMetrics.counter(REQUEST_TIMEOUTS);
        recordsWrittenCounter = pluginMetrics.counter(RECORDS_WRITTEN);
    }


    @Override
    public void export(ExportTraceServiceRequest request, StreamObserver<ExportTraceServiceResponse> responseObserver) {
        try {
            buffer.write(new Record<>(request), bufferWriteTimeoutInMillis);
            responseObserver.onNext(ExportTraceServiceResponse.newBuilder().build());
            responseObserver.onCompleted();
            recordsWrittenCounter.increment();
            pluginMetrics.gauge(BUFFER_SIZE, buffer.read(bufferWriteTimeoutInMillis).size());
        } catch (TimeoutException e) {
            responseObserver
                    .onError(Status.RESOURCE_EXHAUSTED.withDescription("Buffer is full, request timed out.")
                            .asException());
            requestTimeoutCounter.increment();
        }
    }
}
