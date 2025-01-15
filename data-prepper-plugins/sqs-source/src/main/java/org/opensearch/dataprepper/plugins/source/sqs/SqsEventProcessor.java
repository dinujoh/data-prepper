/*
 * Copyright OpenSearch Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * The OpenSearch Contributors require contributions made to
 * this file be licensed under the Apache-2.0 license or a
 * compatible open source license.
 *
 */

package org.opensearch.dataprepper.plugins.source.sqs;

import org.opensearch.dataprepper.model.acknowledgements.AcknowledgementSet;
import org.opensearch.dataprepper.model.buffer.Buffer;
import org.opensearch.dataprepper.model.event.Event;
import org.opensearch.dataprepper.model.record.Record;
import software.amazon.awssdk.services.sqs.model.Message;
import java.io.IOException;

public class SqsEventProcessor {
    private final SqsMessageHandler sqsMessageHandler;
    SqsEventProcessor(final SqsMessageHandler sqsMessageHandler) {
        this.sqsMessageHandler= sqsMessageHandler;
    }

    void addSqsObject(final Message message,
                      final String url,
                      final Buffer<Record<Event>> buffer,
                      final int bufferTimeoutmillis,
                      final AcknowledgementSet acknowledgementSet) throws IOException {
        sqsMessageHandler.handleMessage(message, url, buffer, bufferTimeoutmillis, acknowledgementSet);
    }

}