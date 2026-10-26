# Sample data

All files here are synthetic samples, not real claims. All use CRLF line endings, and none ends
with a final newline.

## pharmacy_data_sample.csv

Pharmacy claims, with a header row. 32,932 data rows, 30,097 of them distinct. Three rows (patient 1488, NDC `0    -    -  `)
have an invalid NDC, so cleaning writes 30,094 rows and rejects 3.

Columns: `Patient_ID`, `Date_Submitted`, `NDC`, `Amount_Billed`, `Amount_Paid`.

- `Date_Submitted` is `m/d/yyyy` with no zero padding (for example `10/24/2016`).
- `NDC` is messy: nearly every value contains dashes (`00591-0845-10`), and a few are missing
  leading zeros once the dashes are removed.
- The package reads every column as a string, so amounts are written back exactly as read.

## medical_data_sample.csv

Medical claims, with a header row. 30,000 data rows, 28,285 of them distinct.

Columns: `Patient_ID`, `Diagnosis_Code_1`, `Diagnosis_Code_2`, `Diagnosis_Code_3`,
`Diagnosis_Code_4`, `Service_Date`, `Amount_Paid`, `Amount_Billed`.

- `Service_Date` is `m/d/yyyy` with no zero padding.
- Unused diagnosis slots are filled with 8 spaces, not left empty. Trim before treating a code as
  missing.
- A claim can carry up to four diagnosis codes.
- The amount columns are in the opposite order from the pharmacy file (`Amount_Paid` first).
- Cleaning (`dedupe-medical`) strips the padding, so each of the 1,715 exact duplicates collapses
  and the output has 28,285 rows. Unused diagnosis slots become empty fields: 0, 12,140, 15,841
  and 18,860 of them in `Diagnosis_Code_1` to `Diagnosis_Code_4`, 46,841 in all.
- Rows that share patient, date and codes but differ in an amount are separate claim lines (patient
  5 on 4/17/2016 has four) and are kept.

## ndc.csv

NDCs for drug X. No header, 500 lines (404 distinct), one 11-digit code per line.

## diagnosis_code.csv

Diagnosis codes for condition Y. No header, 200 lines (146 distinct), one code per line. Some
lines carry trailing spaces. Codes are 3 to 5 digits
with no decimal point.

Because neither reference file has a header, read them with `has_header=False` (polars) or
`header=None` (pandas), and read the NDC column as a string so leading zeros survive.
