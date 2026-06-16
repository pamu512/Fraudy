/**
 * Fraud Tool — Google Sheets add-on script (V8 Engine)
 *
 * Deploy: Extensions -> Apps Script -> paste this file -> save -> reload the sheet.
 * Apps Script cannot reach localhost directly; use an HTTPS tunnel or hosted API.
 */

const DEFAULT_API_BASE_URL = 'https://your-api.example.com';
const DEFAULT_RESULTS_PAGE_SIZE = 2000;
const RISK_SCORE_HEADER = 'Risk Score';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Fraud Tool')
    .addItem('Run Session Analysis', 'runFraudAnalysis')
    .addSeparator()
    .addItem('Set API Endpoint', 'promptForApiUrl')
    .addItem('Set API Token', 'promptForApiToken')
    .addToUi();
}

function promptForApiUrl() {
  const ui = SpreadsheetApp.getUi();
  const currentUrl = getFraudApiBaseUrl_();
  const response = ui.prompt(
    'Configure API Endpoint',
    `Enter the base URL for Fraudy, without a trailing route.\n\nCurrent: ${currentUrl}`,
    ui.ButtonSet.OK_CANCEL,
  );

  if (response.getSelectedButton() !== ui.Button.OK) {
    return;
  }

  const newUrl = normalizeApiBaseUrl_(response.getResponseText().trim());
  if (!newUrl) {
    ui.alert('No Change', 'The API URL was left unchanged.', ui.ButtonSet.OK);
    return;
  }

  if (!/^https?:\/\//i.test(newUrl)) {
    ui.alert(
      'Invalid URL',
      'Enter a full URL beginning with http:// or https://.',
      ui.ButtonSet.OK,
    );
    return;
  }

  PropertiesService.getScriptProperties().setProperty('FRAUD_API_BASE_URL', newUrl);
  ui.alert('Success', 'API URL updated successfully.', ui.ButtonSet.OK);
}

function promptForApiToken() {
  const ui = SpreadsheetApp.getUi();
  const response = ui.prompt(
    'Configure API Token',
    'Enter the X-Fraudy-Token value configured on the backend.',
    ui.ButtonSet.OK_CANCEL,
  );

  if (response.getSelectedButton() !== ui.Button.OK) {
    return;
  }

  const token = response.getResponseText().trim();
  if (!token) {
    ui.alert('No Change', 'The API token was left unchanged.', ui.ButtonSet.OK);
    return;
  }

  PropertiesService.getScriptProperties().setProperty('FRAUDY_API_KEY', token);
  ui.alert('Success', 'API token updated successfully.', ui.ButtonSet.OK);
}

function runFraudAnalysis() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = spreadsheet.getActiveSheet();
  const ui = SpreadsheetApp.getUi();

  try {
    const matrix = readSheetMatrix_(sheet);
    if (!matrix.values.length || !matrix.values[0].length) {
      throw new Error('The active sheet has no data to analyze.');
    }

    const stripped = stripEmptyColumns_(matrix.values);
    if (!stripped.values.length || !stripped.headers.length) {
      throw new Error('All columns are empty after removing blank columns.');
    }

    spreadsheet.toast(
      `Profiling ${stripped.dataRowCount} row(s)...`,
      'Fraud Session',
      -1,
    );

    const csv = buildCsv_(stripped.headers, stripped.hasHeaderRow ? stripped.values.slice(1) : stripped.values);
    const upload = uploadSession_(csv, `${safeFilename_(sheet.getName())}.csv`);

    const includedColumns = [];
    let summaryText = `Fraudy profiled ${upload.total_rows} rows.\n\nDetected Schema:\n`;
    
    upload.profile.columns.forEach(col => {
        let isIncluded = false;
        let typeDisplay = "Unknown";
        
        switch (col.logical_type) {
          case "metadata_id":
            typeDisplay = "Row IDs (Excluded)";
            break;
          case "categorical":
            typeDisplay = "Categories (Excluded)";
            break;
          case "integer":
          case "float":
            typeDisplay = "Numeric (Included)";
            isIncluded = true;
            break;
          case "datetime":
            typeDisplay = "Date/Time (Excluded)";
            break;
          default:
            typeDisplay = `${col.logical_type} (Excluded)`;
        }
        
        if (isIncluded) includedColumns.push(col.name);
        summaryText += `- ${col.name}: ${typeDisplay}\n`;
    });
    
    summaryText += `\nProceed with anomaly detection on ${includedColumns.length} columns?`;
    
    const response = ui.alert('Pre-Flight Checklist', summaryText, ui.ButtonSet.YES_NO);
    if (response !== ui.Button.YES) {
       ui.alert('Analysis Cancelled', 'You can modify the data and try again.', ui.ButtonSet.OK);
       return;
    }

    spreadsheet.toast(
      `Analyzing Session ${upload.session_id.slice(0, 8)}. Downloading scores...`,
      'Fraud Session',
      -1,
    );

    analyzeSession_(upload.session_id, includedColumns);
    const results = fetchAllSessionResults_(upload.session_id, upload.total_rows);
    writeRiskScores_(sheet, matrix, results);

    const nonZeroScores = results.filter((res) => res.score > 0).length;
    ui.alert(
      'Analysis Complete',
      `Processed ${upload.total_rows} row(s) with global model context.\n\n` +
        `Appended risk scores. ${nonZeroScores} row(s) received a non-zero score.`,
      ui.ButtonSet.OK,
    );
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    ui.alert('Analysis Failed', message, ui.ButtonSet.OK);
  }
}

/**
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @return {{values: Array<Array<*>>, startRow: number, startCol: number}}
 */
function readSheetMatrix_(sheet) {
  const range = sheet.getDataRange();
  return {
    values: range.getValues(),
    startRow: range.getRow(),
    startCol: range.getColumn(),
  };
}

/**
 * @param {Array<Array<*>>} values
 * @return {{values: Array<Array<*>>, headers: Array<string>, hasHeaderRow: boolean, dataRowCount: number}}
 */
function stripEmptyColumns_(values) {
  if (!values || !values.length) {
    return { values: [], headers: [], hasHeaderRow: false, dataRowCount: 0 };
  }

  const columnCount = values[0].length;
  const nonEmptyColumnIndexes = [];

  for (let col = 0; col < columnCount; col++) {
    if (values.some((row) => !isBlankCell_(row[col]))) {
      nonEmptyColumnIndexes.push(col);
    }
  }

  if (!nonEmptyColumnIndexes.length) {
    return { values: [], headers: [], hasHeaderRow: false, dataRowCount: 0 };
  }

  const trimmed = values.map((row) =>
    nonEmptyColumnIndexes.map((colIndex) => normalizeCell_(row[colIndex])),
  );
  const hasHeaderRow = trimmed.length > 1 && looksLikeHeaderRow_(trimmed[0]);
  const headers = buildHeaders_(trimmed[0], hasHeaderRow);
  const dataRows = hasHeaderRow ? trimmed.slice(1) : trimmed;

  if (!dataRows.length) {
    throw new Error('No data rows remain after removing empty columns.');
  }

  return {
    values: trimmed,
    headers,
    hasHeaderRow,
    dataRowCount: dataRows.length,
  };
}

/**
 * @param {string} csv
 * @param {string} filename
 * @return {{session_id: string, total_rows: number, profile: Object}}
 */
function uploadSession_(csv, filename) {
  const blob = Utilities.newBlob(csv, 'text/csv', filename);
  const response = UrlFetchApp.fetch(`${getFraudApiBaseUrl_()}/session/upload`, {
    method: 'post',
    headers: buildAuthHeaders_(),
    payload: { file: blob },
    muteHttpExceptions: true,
  });

  return parseJsonResponse_(response, 'Session upload failed');
}

/**
 * @param {string} sessionId
 * @param {Array<string>} includedColumns
 */
function analyzeSession_(sessionId, includedColumns) {
  const response = UrlFetchApp.fetch(`${getFraudApiBaseUrl_()}/session/${encodeURIComponent(sessionId)}/analyze`, {
    method: 'post',
    headers: Object.assign({}, buildAuthHeaders_(), { 'Content-Type': 'application/json' }),
    payload: JSON.stringify({ included_columns: includedColumns }),
    muteHttpExceptions: true,
  });

  return parseJsonResponse_(response, 'Session analysis failed');
}

/**
 * @param {string} sessionId
 * @param {number} totalRows
 * @return {Array<number>}
 */
function fetchAllSessionResults_(sessionId, totalRows) {
  const results = [];
  let skip = 0;

  while (skip < totalRows) {
    SpreadsheetApp.getActiveSpreadsheet().toast(
      `Fetching results ${skip + 1}-${Math.min(skip + DEFAULT_RESULTS_PAGE_SIZE, totalRows)} of ${totalRows}...`,
      'Fraud Session',
      -1,
    );

    const url =
      `${getFraudApiBaseUrl_()}/session/${encodeURIComponent(sessionId)}/results` +
      `?skip=${skip}&limit=${DEFAULT_RESULTS_PAGE_SIZE}`;
    const response = UrlFetchApp.fetch(url, {
      method: 'get',
      headers: buildAuthHeaders_(),
      muteHttpExceptions: true,
    });
    const page = parseJsonResponse_(response, 'Session results fetch failed');
    const pageResults = Array.isArray(page.results) ? page.results : [];
    
    for (const rowRes of pageResults) {
      const reason = Array.isArray(rowRes.hits) 
        ? rowRes.hits.map((h) => h.reason).join(' | ') 
        : '';
      results.push({ score: Number(rowRes.score) || 0, reason: reason });
    }

    if (!pageResults.length) {
      break;
    }
    skip += pageResults.length;
  }

  if (results.length < totalRows) {
    while (results.length < totalRows) {
      results.push({ score: 0, reason: '' });
    }
  }

  return results.slice(0, totalRows);
}

/**
 * @param {GoogleAppsScript.URL_Fetch.HTTPResponse} response
 * @param {string} context
 * @return {Object}
 */
function parseJsonResponse_(response, context) {
  const statusCode = response.getResponseCode();
  const bodyText = response.getContentText();

  if (statusCode < 200 || statusCode >= 300) {
    throw new Error(`${context}: ${extractBackendError_(statusCode, bodyText)}`);
  }

  try {
    return JSON.parse(bodyText);
  } catch (parseError) {
    throw new Error(`${context}: backend returned unparseable JSON.`);
  }
}

/**
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {{values: Array<Array<*>>, startRow: number, startCol: number}} matrix
 * @param {Array<number>} scores
 */
function writeRiskScores_(sheet, matrix, results) {
  const values = matrix.values;
  if (!values.length) {
    return;
  }

  const hasHeaderRow = values.length > 1 && looksLikeHeaderRow_(values[0]);
  const dataRowCount = hasHeaderRow ? values.length - 1 : values.length;
  const scoreColumnIndex = values[0].length + 1;
  const firstDataRowNumber = hasHeaderRow ? matrix.startRow + 1 : matrix.startRow;

  if (hasHeaderRow) {
    sheet
      .getRange(matrix.startRow, scoreColumnIndex, 1, 2)
      .setValues([[RISK_SCORE_HEADER, 'Reason']])
      .setFontWeight('bold');
  }

  const scoreValues = Array.from({ length: dataRowCount }, (_, index) => {
    const res = results[index] || { score: 0, reason: '' };
    return [roundScore_(res.score), res.reason];
  });

  sheet
    .getRange(firstDataRowNumber, scoreColumnIndex, scoreValues.length, 2)
    .setValues(scoreValues);

  sheet.autoResizeColumn(scoreColumnIndex);
  sheet.autoResizeColumn(scoreColumnIndex + 1);
}

/**
 * @param {Array<string>} headers
 * @param {Array<Array<*>>} rows
 * @return {string}
 */
function buildCsv_(headers, rows) {
  return [headers, ...rows]
    .map((row) => row.map((cell) => csvEscape_(cell)).join(','))
    .join('\n');
}

/**
 * @param {*} value
 * @return {string}
 */
function csvEscape_(value) {
  if (value === null || value === undefined) {
    return '';
  }
  const text = String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/**
 * @return {Object<string, string>}
 */
function buildAuthHeaders_() {
  const token = PropertiesService.getScriptProperties().getProperty('FRAUDY_API_KEY');
  if (!token || !token.trim()) {
    throw new Error('Missing API token. Use Fraud Tool -> Set API Token first.');
  }
  return { 'X-Fraudy-Token': token.trim() };
}

/**
 * @return {string}
 */
function getFraudApiBaseUrl_() {
  const configured = PropertiesService.getScriptProperties().getProperty('FRAUD_API_BASE_URL');
  return normalizeApiBaseUrl_(configured && configured.trim() ? configured : DEFAULT_API_BASE_URL);
}

/**
 * @param {string} raw
 * @return {string}
 */
function normalizeApiBaseUrl_(raw) {
  return String(raw || '')
    .replace(/\/+$/, '')
    .replace(/\/analyze$/i, '')
    .replace(/\/session\/upload$/i, '');
}

/**
 * @param {number} statusCode
 * @param {string} bodyText
 * @return {string}
 */
function extractBackendError_(statusCode, bodyText) {
  if (!bodyText) {
    return `HTTP ${statusCode}`;
  }

  try {
    const errorJson = JSON.parse(bodyText);
    if (Array.isArray(errorJson.detail)) {
      return errorJson.detail
        .map((item) => {
          const location = Array.isArray(item.loc) ? item.loc.join('.') : 'payload';
          return `${location}: ${item.msg || JSON.stringify(item)}`;
        })
        .join('; ');
    }
    if (typeof errorJson.detail === 'string') {
      return errorJson.detail;
    }
    return JSON.stringify(errorJson.detail || errorJson).slice(0, 500);
  } catch (parseError) {
    return bodyText.slice(0, 500);
  }
}

/**
 * @param {*} value
 * @return {boolean}
 */
function isBlankCell_(value) {
  return value === null || value === undefined || String(value).trim() === '';
}

/**
 * @param {*} value
 * @return {*}
 */
function normalizeCell_(value) {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  if (typeof value === 'boolean' || typeof value === 'number') {
    return value;
  }
  if (Object.prototype.toString.call(value) === '[object Date]') {
    return Utilities.formatDate(
      value,
      Session.getScriptTimeZone(),
      "yyyy-MM-dd'T'HH:mm:ss",
    );
  }
  return String(value).trim();
}

/**
 * @param {Array<*>} row
 * @return {boolean}
 */
function looksLikeHeaderRow_(row) {
  const stats = row.reduce(
    (acc, cell) => {
      if (!isBlankCell_(cell)) {
        acc.nonEmpty += 1;
        if (typeof cell === 'string' && isNaN(Number(cell.replace(/[,$]/g, '').trim()))) {
          acc.textLike += 1;
        } else if (Object.prototype.toString.call(cell) === '[object Date]') {
          acc.textLike += 1;
        }
      }
      return acc;
    },
    { nonEmpty: 0, textLike: 0 },
  );

  return stats.nonEmpty > 0 && stats.textLike / stats.nonEmpty >= 0.6;
}

/**
 * @param {Array<*>} firstRow
 * @param {boolean} hasHeaderRow
 * @return {Array<string>}
 */
function buildHeaders_(firstRow, hasHeaderRow) {
  const rawHeaders = hasHeaderRow
    ? firstRow.map((cell, index) => sanitizeHeader_(cell, index))
    : firstRow.map((_cell, index) => columnLetter_(index));

  return dedupeHeaders_(rawHeaders);
}

/**
 * @param {*} value
 * @param {number} index
 * @return {string}
 */
function sanitizeHeader_(value, index) {
  if (isBlankCell_(value)) {
    return `Column_${columnLetter_(index)}`;
  }
  return String(value).trim().replace(/\s+/g, ' ').slice(0, 255);
}

/**
 * @param {Array<string>} headers
 * @return {Array<string>}
 */
function dedupeHeaders_(headers) {
  const seen = {};
  return headers.map((header) => {
    seen[header] = (seen[header] || 0) + 1;
    return seen[header] === 1 ? header : `${header}_${seen[header]}`;
  });
}

/**
 * @param {number} index
 * @return {string}
 */
function columnLetter_(index) {
  let letter = '';
  let current = index;
  while (current >= 0) {
    letter = String.fromCharCode((current % 26) + 65) + letter;
    current = Math.floor(current / 26) - 1;
  }
  return letter;
}

/**
 * @param {string} value
 * @return {string}
 */
function safeFilename_(value) {
  return String(value || 'sheet').replace(/[^a-z0-9_-]+/gi, '_').slice(0, 80);
}

/**
 * @param {number} score
 * @return {number}
 */
function roundScore_(score) {
  return Math.round(score * 10000) / 10000;
}
