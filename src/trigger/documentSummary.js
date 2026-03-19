import { logger, task } from "@trigger.dev/sdk/v3";

export const documentSummaryTask = task({
  id: "document-summary",
  maxDuration: 300,
  run: async (payload) => {
    logger.info("Processing document summary", payload);

    if (!payload?.callbackUrl) {
      throw new Error("callbackUrl is required");
    }

    if (!payload?.internalSecret) {
      throw new Error("internalSecret is required");
    }

    // Trigger.dev manages scheduling and retries, then calls back into Python.
    const response = await fetch(payload.callbackUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-internal-secret": payload.internalSecret,
      },
      body: JSON.stringify({
        documentId: payload.documentId,
        userId: payload.userId,
        jobId: payload.jobId,
      }),
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`Python summary callback failed: ${detail}`);
    }

    const result = await response.json();

    return {
      summary: result.summary,
      documentId: payload.documentId,
      jobId: payload.jobId,
    };
  },
});
