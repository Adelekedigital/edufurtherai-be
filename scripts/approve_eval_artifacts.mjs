import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const jsonlPath = "evaluations/samples.jsonl";
const rows = (await fs.readFile(jsonlPath, "utf8")).trim().split("\n").map(JSON.parse);
for (const row of rows) {
  row.review_status = "APPROVED";
}
await fs.writeFile(jsonlPath, rows.map((row) => JSON.stringify(row)).join("\n") + "\n");

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load("outputs/evaluation-set-template.xlsx"));
const sheet = workbook.worksheets.getItem("Evaluation Samples");
sheet.getRange(`F2:F${rows.length + 1}`).values = rows.map(() => ["APPROVED"]);
sheet.getRange(`I2:I${rows.length + 1}`).values = rows.map((row) => [JSON.stringify(row.expected)]);
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save("outputs/evaluation-set-template.xlsx");
console.log(`approved ${rows.length} evaluation cases`);
