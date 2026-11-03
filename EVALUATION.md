# Evaluation

Command run:

```powershell
cd backend
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q -p no:cacheprovider
```

Actual result: `16 passed, 14 warnings in 16.66s`.

Warnings were from Pydantic `json_encoders`, FastAPI `on_event`, and EasyOCR/Torch deprecations. The pytest cache provider was disabled because it hung at process shutdown in this environment after all assertions completed.

The default sample workspace is loaded automatically when the local database is empty. Matching verifier files are in `test_uploads_2/`.

Frontend production build command:

```powershell
cd frontend
npm run build
```

Actual result: passed. The first build attempt hit a Windows `EPERM` lock while clearing the ignored `frontend/dist/assets` folder; rerunning the same command with filesystem permission completed successfully.

## Reconciliation Cases

| Case | Expected behavior | Actual result |
| --- | --- | --- |
| Exact match | Invoice paid when reference, date, and amount match | Passed |
| Partial payment | Invoice marked partially paid with outstanding balance | Passed |
| Unpaid invoice | Invoice marked unpaid when no candidate exists | Passed |
| Unmatched bank transaction | Outgoing bank transaction gets unmatched-bank result | Passed |
| Duplicate invoice candidate | Duplicate invoice number and amount require review | Passed |
| Vendor-name variation | Vendor token match can reconcile without exact vendor spelling | Passed |
| Transaction outside allowed date window | Late transaction ignored and invoice remains unpaid | Passed |
| Amount mismatch | Near amount mismatch requires review | Passed |
| Missing invoice number | Match candidate exists but invoice requires review | Passed |
| Ambiguous candidate match | Multiple exact candidates require review | Passed |

## Additional Tests

| Area | Expected behavior | Actual result |
| --- | --- | --- |
| CSV parsing | Common bank CSV headers produce transaction records and row references | Passed |
| PDF extraction | Text-based PDF fields are extracted | Passed |
| Scanned/empty PDF handling | Local OCR is attempted; unreadable files return a clear message | Passed |
| Review persistence | Review decision is stored separately from suggestion | Passed |
| Citation/source references | Q&A answer includes source citations with row/page locators | Passed |
| CSV export | Report includes suggestions, final status, and evidence fields | Passed |

## Known Failure Modes

- Low-quality scanned PDFs depend on local OCR quality.
- Very unusual invoice layouts may need parser or SLM prompt additions.
- Bank CSVs with uncommon localized headers may need mapper additions.
- Vendor token matching can miss abbreviations not present in the source text.
- The prototype does not attempt tax/GST validation.

