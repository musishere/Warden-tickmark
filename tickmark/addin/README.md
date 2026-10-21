# Tickmark in Excel

A task pane add-in. Select the sample in your sheet, pick the invoice PDFs and press **Check sample**. Tickmark writes **Tickmark | Why | Invoice** next to your data, one colour per verdict, and the invoice cell links to the PDF.

The add-in is only a front end. It calls the same backend and API as the web page, so the verdicts are the same.

## Run it locally

```bash
make certs        # once: a localhost https certificate your Mac trusts (asks for your password)
make run-https    # the backend at https://localhost:8000
```

Open https://localhost:8000/static/addin/taskpane.html once in your browser. It should load without a certificate warning; if not, run `make certs` again.

## Load it into Excel (sideload)

**Excel on the web** (excel.office.com):
1. Open any workbook stored in OneDrive or SharePoint.
2. **Home → Add-ins → More Add-ins** (or **Insert → Add-ins**), then **My Add-ins → Upload My Add-in**.
3. Choose `addin/manifest.xml`. A **Tickmark → Vouch sample** button appears on the Home tab.

**Excel for Mac** (desktop):
1. Copy the manifest:
   `cp addin/manifest.xml ~/Library/Containers/com.microsoft.Excel/Data/Documents/wef/`
   (create the `wef` folder if it isn't there)
2. Restart Excel, open a workbook, then **Home → Add-ins**. Tickmark is listed under developer add-ins.

**Excel for Windows:** share a folder containing `manifest.xml`, add it under *Trust Center → Trusted Add-in Catalogs*, restart Excel, then **Insert → My Add-ins → Shared Folder**.

## Use it

1. Select the sample, **including its header row**. If a single cell is selected, the sheet's used range is taken.
2. Press **Use selected range**.
3. Choose the invoice PDFs.
4. Set the period, currency and VAT rates, the same as on the web page.
5. Press **Check sample**. If some columns aren't recognised by name, the pane asks which column holds what; answer and press again.
6. Results:
   - Three columns appear to the right of the selection, coloured: ✅ green, ⚠️ red, 👀 amber, ❓ grey.
   - The invoice cell links to the PDF.
   - Click a row in the pane to select it in the sheet and open its invoice.

## Test data

Any of these work. Open the `sample.xlsx`, select the data, and pick the PDFs from its `invoices/` folder:

| Folder | Period | Currency | VAT % |
|---|---|---|---|
| `data/real/facturx/` | 2013-01-01 → 2020-12-31 | EUR | 0,5.5,7,19,20 |
| `data/real/variety/originals/` | 2012-02-24 → 2023-11-03 | EUR | 0,2.1,5.5,7,8.5,10,19,20 |
| `data/real/variety/scans/` | 2014-05-19 → 2019-10-31 | EUR | 0,2.1,5.5,7,8.5,10,19,20 |
| `data/real/variety/photos/` | 2014-05-19 → 2018-12-20 | EUR | 0,2.1,5.5,7,8.5,10,19,20 |

The expected answers are in each folder's `truth.json`.

## Not in this first version
- Highlighting the value on the PDF (next step: Tickmark already stores where each value was found).
- Recording a reviewer's decision from the sheet (the API has it: `POST /api/runs/{id}/reviews`).
- Saving runs to an engagement from Excel.
- Hosting for a firm: the add-in and backend must be on a real https address, and IT deploys the manifest through the Microsoft 365 admin center.
