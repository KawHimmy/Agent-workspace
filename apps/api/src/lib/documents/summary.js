import { buildPlainSummary, extractDocumentText } from "./parser.js";
import { summarizeTextWithLLM } from "../llm.js";
import { getDocumentById, updateBackgroundJob, updateDocument } from "../storage/appStore.js";

export async function processDocumentSummary({ documentId, userId, jobId }) {
  const document = await getDocumentById(documentId, userId);

  if (!document) {
    throw new Error("Document not found");
  }

  await updateDocument(documentId, { status: "processing" });
  await updateBackgroundJob(jobId, { status: "processing" });

  const extraction = await extractDocumentText(document.filePath, document.originalName);
  const llmSummary = await summarizeTextWithLLM(extraction.text);
  const summary = llmSummary ?? buildPlainSummary(extraction.text);

  await updateDocument(documentId, {
    status: "completed",
    extractedText: extraction.text,
    summary,
  });

  await updateBackgroundJob(jobId, {
    status: "completed",
    output: { summary },
  });

  return summary;
}
