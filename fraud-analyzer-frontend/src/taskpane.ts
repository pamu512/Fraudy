/* global Office, Excel */

type CellPrimitive = string | number | boolean | null;
type WritebackMode = "highlight" | "sheet";

interface ColumnProfile {
  name: string;
  logical_type: string;
}

interface SessionUploadResponse {
  session_id: string;
  total_rows: number;
  profile: {
    columns: ColumnProfile[];
  };
}

interface SessionAnalyzeResponse {
  session_id: string;
  message: string;
}

interface RuleHit {
  rule_name: string;
  passed: boolean;
  score: number;
  reason: string;
  affected_columns: string[];
  affected_rows: number[];
  details?: Record<string, unknown>;
}

interface SessionRowResult {
  row_index: number;
  score: number;
  is_anomaly: boolean;
  hits: RuleHit[];
}

interface SessionResultsResponse {
  session_id: string;
  skip: number;
  limit: number;
  total_rows: number;
  results: SessionRowResult[];
}

interface RiskScoreRow {
  rowIndex: number;
  score: number;
  reason: string;
}

interface SelectionSnapshot {
  headers: string[];
  rows: CellPrimitive[][];
  rangeAddress: string;
  sheetName: string;
  rowCount: number;
  columnCount: number;
  hasHeaderRow: boolean;
  dataStartRow: number;
  dataStartColumn: number;
}

const DEFAULT_API_BASE_URL = "http://localhost:8000";
const DEFAULT_API_TOKEN = "super-secret-local-token";
const RESULTS_PAGE_SIZE = 2000;
const RISK_SCORE_HEADER = "Risk Score";
const HIGH_RISK_SCORE_THRESHOLD = 0.75;
const HIGHLIGHT_FILL = "#FCE8E6";
const HIGHLIGHT_FONT = "#A4262C";
const RESULTS_SHEET_NAME = "Fraud Results";

const backendUrlInput = document.getElementById("backend-url") as HTMLInputElement | null;
const apiTokenInput = document.getElementById("api-token") as HTMLInputElement | null;
const statusEl = document.getElementById("status") as HTMLParagraphElement | null;
const selectionRangeEl = document.getElementById("selection-range") as HTMLElement | null;
const selectionSheetEl = document.getElementById("selection-sheet") as HTMLElement | null;
const selectionShapeEl = document.getElementById("selection-shape") as HTMLElement | null;
const selectionHeadersEl = document.getElementById("selection-headers") as HTMLElement | null;
const profileBtn = document.getElementById("profile-btn") as HTMLButtonElement | null;
const analyzeBtn = document.getElementById("analyze-btn") as HTMLButtonElement | null;
const refreshBtn = document.getElementById("refresh-selection-btn") as HTMLButtonElement | null;
const profilePanel = document.getElementById("profile-panel") as HTMLElement | null;
const profileList = document.getElementById("profile-list") as HTMLUListElement | null;
const summaryPanel = document.getElementById("summary-panel") as HTMLElement | null;
const summaryContent = document.getElementById("summary-content") as HTMLElement | null;
const anomalyPanel = document.getElementById("anomaly-panel") as HTMLElement | null;
const anomalyList = document.getElementById("anomaly-list") as HTMLUListElement | null;

let currentSelection: SelectionSnapshot | null = null;
let currentSessionId: string | null = null;
let currentTotalRows: number = 0;
let currentIncludedColumns: string[] = [];

function setStatus(message: string, tone: "default" | "error" | "success" = "default"): void {
  if (!statusEl) {
    return;
  }
  statusEl.textContent = message;
  statusEl.classList.remove("error", "success");
  if (tone === "error") {
    statusEl.classList.add("error");
  }
  if (tone === "success") {
    statusEl.classList.add("success");
  }
}

function getApiBaseUrl(): string {
  const raw = backendUrlInput?.value.trim() || DEFAULT_API_BASE_URL;
  return raw
    .replace(/\/+$/, "")
    .replace(/\/analyze$/i, "")
    .replace(/\/session\/upload$/i, "");
}

function getApiToken(): string {
  return apiTokenInput?.value.trim() || DEFAULT_API_TOKEN;
}

function getWritebackMode(): WritebackMode {
  const selected = document.querySelector<HTMLInputElement>(
    'input[name="writeback-mode"]:checked',
  );
  return selected?.value === "sheet" ? "sheet" : "highlight";
}

function columnIndexToLetter(index: number): string {
  let letter = "";
  let current = index;
  while (current >= 0) {
    letter = String.fromCharCode((current % 26) + 65) + letter;
    current = Math.floor(current / 26) - 1;
  }
  return letter;
}

function sanitizeHeader(value: CellPrimitive, index: number): string {
  const fallback = `Column_${columnIndexToLetter(index)}`;
  if (value === null || value === undefined) {
    return fallback;
  }

  const cleaned = String(value).trim().replace(/\s+/g, " ");
  return cleaned ? cleaned.slice(0, 255) : fallback;
}

function normalizeCellValue(value: Excel.CellValue): CellPrimitive {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "object") {
    if ("error" in value && value.error) {
      return null;
    }
    if ("text" in value && typeof value.text === "string") {
      return value.text;
    }
    if ("result" in value) {
      const nested = value.result;
      if (
        typeof nested === "string" ||
        typeof nested === "number" ||
        typeof nested === "boolean"
      ) {
        return nested;
      }
    }
  }
  return String(value);
}

function looksLikeHeaderRow(row: CellPrimitive[]): boolean {
  if (row.length === 0) {
    return false;
  }

  let nonEmpty = 0;
  let textLike = 0;
  for (const cell of row) {
    if (cell === null || cell === "") {
      continue;
    }
    nonEmpty += 1;
    if (typeof cell === "string") {
      const numericCandidate = cell.replace(/[,$]/g, "").trim();
      if (numericCandidate === "" || Number.isNaN(Number(numericCandidate))) {
        textLike += 1;
      }
    }
  }
  return nonEmpty > 0 && textLike / nonEmpty >= 0.6;
}

function buildUniqueHeaders(rawHeaders: string[]): string[] {
  const seen = new Map<string, number>();
  return rawHeaders.map((header) => {
    const count = seen.get(header) ?? 0;
    seen.set(header, count + 1);
    return count === 0 ? header : `${header}_${count + 1}`;
  });
}

function serializeSelection(values: CellPrimitive[][], hasHeaderRow: boolean): {
  headers: string[];
  rows: CellPrimitive[][];
} {
  if (values.length === 0) {
    throw new Error("The selected range is empty.");
  }

  const headerSource = hasHeaderRow ? values[0] : values[0].map(() => null);
  const headers = buildUniqueHeaders(
    headerSource.map((cell, index) => sanitizeHeader(cell, index)),
  );
  const rows = hasHeaderRow ? values.slice(1) : values;

  if (rows.length === 0) {
    throw new Error("No data rows found below the header row.");
  }

  return { headers, rows };
}

async function readActiveSelection(): Promise<SelectionSnapshot> {
  return Excel.run(async (context: Excel.RequestContext) => {
    const range = context.workbook.getSelectedRange();
    const worksheet = range.worksheet;
    range.load(["values", "address", "rowIndex", "columnIndex", "rowCount", "columnCount"]);
    worksheet.load("name");
    await context.sync();

    if (range.rowCount === 0 || range.columnCount === 0) {
      throw new Error("The active selection is empty.");
    }

    const normalized = (range.values as Excel.CellValue[][]).map((row) =>
      row.map((cell) => normalizeCellValue(cell)),
    );
    const hasHeaderRow = normalized.length > 1 && looksLikeHeaderRow(normalized[0]);
    const { headers, rows } = serializeSelection(normalized, hasHeaderRow);

    return {
      headers,
      rows,
      rangeAddress: range.address,
      sheetName: worksheet.name,
      rowCount: rows.length,
      columnCount: headers.length,
      hasHeaderRow,
      dataStartRow: hasHeaderRow ? range.rowIndex + 1 : range.rowIndex,
      dataStartColumn: range.columnIndex,
    };
  });
}

function updateSelectionUi(snapshot: SelectionSnapshot | null): void {
  if (!profileBtn) {
    return;
  }

  if (!snapshot) {
    if (selectionRangeEl) selectionRangeEl.textContent = "-";
    if (selectionSheetEl) selectionSheetEl.textContent = "-";
    if (selectionShapeEl) selectionShapeEl.textContent = "-";
    if (selectionHeadersEl) selectionHeadersEl.textContent = "-";
    profileBtn.disabled = true;
    return;
  }

  if (selectionRangeEl) selectionRangeEl.textContent = snapshot.rangeAddress;
  if (selectionSheetEl) selectionSheetEl.textContent = snapshot.sheetName;
  if (selectionShapeEl) {
    selectionShapeEl.textContent = `${snapshot.rowCount} rows x ${snapshot.columnCount} columns`;
  }
  if (selectionHeadersEl) {
    selectionHeadersEl.textContent = snapshot.headers.join(", ");
  }
  profileBtn.disabled = false;
}

async function refreshSelection(): Promise<void> {
  setStatus("Reading active selection...");
  try {
    currentSelection = await readActiveSelection();
    updateSelectionUi(currentSelection);
    setStatus("Selection ready.", "success");
  } catch (error) {
    currentSelection = null;
    updateSelectionUi(null);
    const message = error instanceof Error ? error.message : "Failed to read selection.";
    setStatus(message, "error");
  }
}

function csvEscape(value: CellPrimitive): string {
  if (value === null || value === undefined) {
    return "";
  }
  const text = String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function buildCsv(snapshot: SelectionSnapshot): string {
  return [snapshot.headers, ...snapshot.rows]
    .map((row) => row.map((cell) => csvEscape(cell)).join(","))
    .join("\n");
}

function safeFilename(name: string): string {
  return `${name.replace(/[^a-z0-9_-]+/gi, "_").slice(0, 80) || "selection"}.csv`;
}

async function parseJsonResponse<T>(response: Response, context: string): Promise<T> {
  const bodyText = await response.text();

  if (!response.ok) {
    throw new Error(`${context}: ${extractBackendError(response.status, bodyText)}`);
  }

  try {
    return JSON.parse(bodyText) as T;
  } catch {
    throw new Error(`${context}: backend returned invalid JSON.`);
  }
}

function extractBackendError(statusCode: number, bodyText: string): string {
  if (!bodyText) {
    return `HTTP ${statusCode}`;
  }

  try {
    const errorBody = JSON.parse(bodyText) as {
      detail?: string | { loc?: string[]; msg?: string }[];
    };
    if (Array.isArray(errorBody.detail)) {
      return errorBody.detail
        .map((item) => `${item.loc?.join(".") ?? "payload"}: ${item.msg ?? JSON.stringify(item)}`)
        .join("; ");
    }
    if (typeof errorBody.detail === "string") {
      return errorBody.detail;
    }
    return JSON.stringify(errorBody).slice(0, 500);
  } catch {
    return bodyText.slice(0, 500);
  }
}

async function uploadSession(snapshot: SelectionSnapshot): Promise<SessionUploadResponse> {
  const csv = buildCsv(snapshot);
  const formData = new FormData();
  formData.append(
    "file",
    new Blob([csv], { type: "text/csv" }),
    safeFilename(snapshot.sheetName),
  );

  const response = await fetch(`${getApiBaseUrl()}/session/upload`, {
    method: "POST",
    headers: { "X-Fraudy-Token": getApiToken() },
    body: formData,
  });

  return parseJsonResponse<SessionUploadResponse>(response, "Session upload failed");
}

async function fetchSessionResults(sessionId: string, totalRows: number): Promise<SessionRowResult[]> {
  const results: SessionRowResult[] = [];
  let skip = 0;

  while (skip < totalRows) {
    setStatus(
      `Fetching results ${skip + 1}-${Math.min(skip + RESULTS_PAGE_SIZE, totalRows)} of ${totalRows}...`,
    );

    const url = `${getApiBaseUrl()}/session/${encodeURIComponent(
      sessionId,
    )}/results?skip=${skip}&limit=${RESULTS_PAGE_SIZE}`;
    const response = await fetch(url, {
      method: "GET",
      headers: { "X-Fraudy-Token": getApiToken() },
    });
    const page = await parseJsonResponse<SessionResultsResponse>(
      response,
      "Session results fetch failed",
    );

    if (page.results.length === 0) {
      break;
    }

    results.push(...page.results);
    skip += page.results.length;
  }

  while (results.length < totalRows) {
    results.push({ row_index: results.length, score: 0, is_anomaly: false, hits: [] });
  }
  return results.slice(0, totalRows);
}

function resultsToHighRiskRows(results: SessionRowResult[]): RiskScoreRow[] {
  return results
    .filter((res) => res.is_anomaly && res.score >= HIGH_RISK_SCORE_THRESHOLD)
    .map((res) => ({
      rowIndex: res.row_index,
      score: res.score,
      reason: res.hits.map((h) => h.reason).join(" | "),
    }))
    .sort((a, b) => b.score - a.score);
}

async function analyzeSession(sessionId: string, includedColumns: string[]): Promise<SessionAnalyzeResponse> {
  const response = await fetch(`${getApiBaseUrl()}/session/${encodeURIComponent(sessionId)}/analyze`, {
    method: "POST",
    headers: {
      "X-Fraudy-Token": getApiToken(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ included_columns: includedColumns }),
  });
  return parseJsonResponse<SessionAnalyzeResponse>(response, "Session analysis failed");
}

function renderProfileChecklist(upload: SessionUploadResponse): void {
  if (!profileList || !profilePanel) return;
  profileList.innerHTML = "";
  currentIncludedColumns = [];
  
  upload.profile.columns.forEach((col) => {
    const item = document.createElement("li");
    item.className = "anomaly-item";
    
    let isIncluded = false;
    let typeDisplay = "Unknown";
    
    switch (col.logical_type) {
      case "metadata_id":
        typeDisplay = "Row IDs (Excluded from ML)";
        break;
      case "categorical":
        typeDisplay = "Categories (Included for Grouping)";
        break;
      case "integer":
      case "float":
        typeDisplay = "Numeric Amount (Included)";
        isIncluded = true;
        break;
      case "datetime":
        typeDisplay = "Date/Time (Excluded from ML)";
        break;
      default:
        typeDisplay = `${col.logical_type} (Excluded)`;
    }
    
    if (isIncluded) {
      currentIncludedColumns.push(col.name);
    }

    const header = document.createElement("header");
    header.innerHTML = `
      <strong>${col.name}</strong>
      <span class="badge ${isIncluded ? 'pass' : 'fail'}">${isIncluded ? 'Included' : 'Excluded'}</span>
    `;

    const reason = document.createElement("p");
    reason.textContent = `Detected as: ${typeDisplay}`;

    item.appendChild(header);
    item.appendChild(reason);
    profileList.appendChild(item);
  });
  
  profilePanel.classList.remove("hidden");
  if (summaryPanel) summaryPanel.classList.add("hidden");
  if (anomalyPanel) anomalyPanel.classList.add("hidden");
}

function renderSummary(upload: SessionUploadResponse, highRiskCount: number): void {
  if (!summaryPanel || !summaryContent) {
    return;
  }

  summaryContent.innerHTML = `
    <div class="summary-item">
      <strong>Session</strong>
      ${upload.session_id.slice(0, 8)}
    </div>
    <div class="summary-item">
      <strong>Total Rows</strong>
      ${upload.total_rows}
    </div>
    <div class="summary-item">
      <strong>Analyzed Columns</strong>
      ${currentIncludedColumns.length}
    </div>
    <div class="summary-item">
      <strong>High-Risk Rows</strong>
      ${highRiskCount}
    </div>
  `;
  summaryPanel.classList.remove("hidden");
}

function renderHighRiskRows(rows: RiskScoreRow[]): void {
  if (!anomalyPanel || !anomalyList) {
    return;
  }

  anomalyList.innerHTML = "";
  anomalyPanel.classList.remove("hidden");

  if (rows.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-state";
    empty.textContent = `No rows reached the ${HIGH_RISK_SCORE_THRESHOLD.toFixed(2)} risk threshold.`;
    anomalyList.appendChild(empty);
    return;
  }

  for (const row of rows.slice(0, 25)) {
    const item = document.createElement("li");
    item.className = "anomaly-item";

    const header = document.createElement("header");
    header.innerHTML = `
      <strong>Row ${row.rowIndex + 1}</strong>
      <span class="badge fail">Score ${row.score.toFixed(2)}</span>
    `;

    const reason = document.createElement("p");
    reason.textContent = row.reason || "Global session model scored this row above the high-risk threshold.";

    item.appendChild(header);
    item.appendChild(reason);
    anomalyList.appendChild(item);
  }
}

async function clearPreviousHighlights(snapshot: SelectionSnapshot): Promise<void> {
  await Excel.run(async (context: Excel.RequestContext) => {
    const sheet = context.workbook.worksheets.getItem(snapshot.sheetName);
    const dataRange = sheet.getRangeByIndexes(
      snapshot.dataStartRow,
      snapshot.dataStartColumn,
      snapshot.rowCount,
      snapshot.columnCount + 2,
    );
    dataRange.format.fill.clear();
    dataRange.format.font.color = "#000000";
    dataRange.conditionalFormats.clearAll();
    await context.sync();
  });
}

async function writeScoresToSourceSheet(
  snapshot: SelectionSnapshot,
  results: SessionRowResult[],
): Promise<void> {
  await Excel.run(async (context: Excel.RequestContext) => {
    const sheet = context.workbook.worksheets.getItem(snapshot.sheetName);
    const scoreColumn = snapshot.dataStartColumn + snapshot.columnCount;
    const headerRow = snapshot.hasHeaderRow ? snapshot.dataStartRow - 1 : snapshot.dataStartRow;

    const headerRange = sheet.getRangeByIndexes(headerRow, scoreColumn, 1, 2);
    headerRange.values = [[RISK_SCORE_HEADER, "Reason"]];
    headerRange.format.font.bold = true;

    const targetRange = sheet.getRangeByIndexes(
      snapshot.dataStartRow,
      scoreColumn,
      snapshot.rowCount,
      2,
    );
    
    // Batch write to grid
    targetRange.values = results.map((res) => {
      const reason = res.hits.map((h) => h.reason).join(" | ");
      return [roundScore(res.score), reason];
    });

    const riskScoreRange = sheet.getRangeByIndexes(
      snapshot.dataStartRow,
      scoreColumn,
      snapshot.rowCount,
      1,
    );

    const conditionalFormat = riskScoreRange.conditionalFormats.add(Excel.ConditionalFormatType.colorScale);
    conditionalFormat.colorScale.criteria = {
      minimum: { type: Excel.ConditionalFormatColorCriterionType.lowestValue, color: "#FFFFFF" },
      midpoint: { type: Excel.ConditionalFormatColorCriterionType.percentile, formula: "50", color: "#FFEB3B" },
      maximum: { type: Excel.ConditionalFormatColorCriterionType.highestValue, color: "#F44336" },
    };

    targetRange.format.autofitColumns();
    await context.sync();
  });
}

async function writeResultsSheet(
  snapshot: SelectionSnapshot,
  results: SessionRowResult[],
  highRiskRows: RiskScoreRow[],
): Promise<void> {
  await Excel.run(async (context: Excel.RequestContext) => {
    const sheets = context.workbook.worksheets;
    let resultsSheet: Excel.Worksheet;

    const existing = sheets.getItemOrNullObject(RESULTS_SHEET_NAME);
    await context.sync();

    if (existing.isNullObject) {
      resultsSheet = sheets.add(RESULTS_SHEET_NAME);
    } else {
      resultsSheet = existing;
      const used = resultsSheet.getUsedRangeOrNullObject();
      await context.sync();
      if (!used.isNullObject) {
        used.clear();
      }
    }

    const highRiskSet = new Set(highRiskRows.map((row) => row.rowIndex));
    const outputHeaders = [...snapshot.headers, "Risk Flag", RISK_SCORE_HEADER, "Reason"];
    const outputRows = snapshot.rows.map((row, index) => {
      const res = results[index];
      const isHighRisk = highRiskSet.has(index);
      const reason = res ? res.hits.map((h) => h.reason).join(" | ") : "";
      return [
        ...row,
        isHighRisk ? "HIGH RISK" : "OK",
        res ? roundScore(res.score) : 0,
        reason,
      ];
    });
    const tableValues = [outputHeaders, ...outputRows];

    const targetRange = resultsSheet.getRangeByIndexes(
      0,
      0,
      tableValues.length,
      outputHeaders.length,
    );
    targetRange.values = tableValues;

    const headerRange = resultsSheet.getRangeByIndexes(0, 0, 1, outputHeaders.length);
    headerRange.format.fill.color = "#0078D4";
    headerRange.format.font.color = "#FFFFFF";
    headerRange.format.font.bold = true;

    for (const highRisk of highRiskRows) {
      const rowRange = resultsSheet.getRangeByIndexes(
        highRisk.rowIndex + 1,
        0,
        1,
        outputHeaders.length,
      );
      rowRange.format.fill.color = HIGHLIGHT_FILL;
      rowRange.format.font.color = HIGHLIGHT_FONT;
    }

    resultsSheet.activate();
    targetRange.format.autofitColumns();
    await context.sync();
  });
}

async function writeSessionResultsToWorkbook(
  snapshot: SelectionSnapshot,
  results: SessionRowResult[],
  mode: WritebackMode,
): Promise<RiskScoreRow[]> {
  const highRiskRows = resultsToHighRiskRows(results);

  if (mode === "highlight") {
    await clearPreviousHighlights(snapshot);
    await writeScoresToSourceSheet(snapshot, results);
    return highRiskRows;
  }

  await writeResultsSheet(snapshot, results, highRiskRows);
  return highRiskRows;
}

async function profileSelection(): Promise<void> {
  if (!currentSelection) {
    setStatus("Select a range in the workbook first.", "error");
    return;
  }

  if (profileBtn) profileBtn.disabled = true;
  if (refreshBtn) refreshBtn.disabled = true;
  if (analyzeBtn) analyzeBtn.disabled = true;

  try {
    setStatus("Profiling selection data...");
    const upload = await uploadSession(currentSelection);
    currentSessionId = upload.session_id;
    currentTotalRows = upload.total_rows;
    renderProfileChecklist(upload);
    setStatus("Profile ready. Review the checklist and run full analysis.", "success");
  } catch (error) {
    if (error instanceof TypeError) {
      setStatus("Network error. Check the backend URL, token, and CORS settings.", "error");
    } else {
      const message = error instanceof Error ? error.message : "Profiling failed.";
      setStatus(message, "error");
    }
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
    if (profileBtn) profileBtn.disabled = currentSelection === null;
    if (analyzeBtn && currentSessionId) analyzeBtn.disabled = false;
  }
}

async function analyzeAndWriteBack(): Promise<void> {
  if (!currentSelection || !currentSessionId) {
    setStatus("Profile the data first.", "error");
    return;
  }

  if (analyzeBtn) analyzeBtn.disabled = true;
  if (profileBtn) profileBtn.disabled = true;
  if (refreshBtn) refreshBtn.disabled = true;

  try {
    setStatus("Running anomaly detection engines...");
    await analyzeSession(currentSessionId, currentIncludedColumns);

    setStatus("Fetching session risk results...");
    const results = await fetchSessionResults(currentSessionId, currentTotalRows);

    setStatus("Writing risk scores to workbook...");
    const highRiskRows = await writeSessionResultsToWorkbook(
      currentSelection,
      results,
      getWritebackMode(),
    );

    // Re-render summary with current upload data mock
    renderSummary({ session_id: currentSessionId, total_rows: currentTotalRows, profile: { columns: [] } }, highRiskRows.length);
    renderHighRiskRows(highRiskRows);
    
    if (profilePanel) profilePanel.classList.add("hidden");
    
    setStatus(
      `Analysis complete. Wrote ${results.length} score(s), ${highRiskRows.length} high-risk row(s).`,
      "success",
    );
  } catch (error) {
    if (error instanceof TypeError) {
      setStatus("Network error. Check the backend URL, token, and CORS settings.", "error");
    } else {
      const message = error instanceof Error ? error.message : "Analysis failed.";
      setStatus(message, "error");
    }
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
    if (profileBtn) profileBtn.disabled = currentSelection === null;
    if (analyzeBtn) analyzeBtn.disabled = false;
  }
}

function roundScore(score: number): number {
  return Math.round(score * 10000) / 10000;
}

function bindEvents(): void {
  refreshBtn?.addEventListener("click", () => {
    void refreshSelection();
  });

  profileBtn?.addEventListener("click", () => {
    void profileSelection();
  });

  analyzeBtn?.addEventListener("click", () => {
    void analyzeAndWriteBack();
  });
}

Office.onReady((info) => {
  if (info.host !== Office.HostType.Excel) {
    setStatus("This add-in only runs in Excel.", "error");
    return;
  }

  bindEvents();
  void refreshSelection();
});
