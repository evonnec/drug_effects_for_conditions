# Drug Effects For Conditions

Data Engineering Challenge

Health care company often deals with claims data, broken out into medical and pharmacy records,
to map out the longitudinal clinical history of patients. In this fake use case, we are looking to
identify the patients who have developed a certain condition (Let’s call it Y), after taking a
particular drug (Let’s call it X). To allow such effort, the data engineering team is asked to help
with the data set dummy_data.csv (attached):

It has four tabs:
● medical_data_sample
● pharmacy_data_sample
● ndc
● diagnosis_code

Medical and pharmacy are typically managed by separate entities, so that’s why the data often
comes in two pieces. The Medical data covers the billing information for doctor visits, surgeries,
diagnostics, usually submitted by medical providers, and the pharmacy data covers the billing
information of prescription drugs and therapies. For example, if a patient gets a prescription from
CVS, this record would appear in the pharmacy claim data, as opposed to the medical.

For the medical_data:
- Timestamped via Service_Date
- The underlying cause of the medical service is identified by diagnosis code. Because in reality
the underlying diagnosis can be complex, there can be more than one diagnosis code recorded
in one claim

For the prescription_data:
- Timestamped via Date_Submitted
- Drug / therapy is identified via NDC (national drug code)
- NDC is typically 11-digit, and usually not clean data.Common issues we see are dashes
between the numbers, and missing 0 on the front, among others. For the scope of this
exercise, let’s consider these two as the only two issues.

Additionally, the ndc tab provides the list of NDC codes for the Drug X
And the diagnosis_code tab provides the list of diagnosis_code for the Condition Y

Question 1:

The claims data comes with duplicates and NDCs are messy. Please write a script to de-
deduplicate the data (Note:rows identical to each other in every single column is considered duplicate) and clean the NDC into standard format (11 digits and matchable to the format in tab ndc)

Question 2 :

In production, we deal with data at scale ingested from various data sources. How would you go
about designing a testing framework to ensure that this data is clean after being processed?

Please share a plan that is as granular and operation-able as possible.Feel free to start high-
level, zoom into one data field, e.g. NDC, and flesh it out to show how you would design it, using it as an example.

Question 3:

Is your de-duplication script optimized for large-scale data? If not, how would you go about
optimizing it? Please describe the algorithm step=by-step (pseudo-code is fine)

Short Questions

1. There are often multiple sources of truth depending on where the information is from. For
example, the chat bot on our website records inquirer’s name, email and a short message.
In the scenario where the inquirer is also a product user, the email reported on the chat bot
might or might not fully agree with the record of that user in the backend database. How do
you go about designing a warehouse storing information from both sources? What aspects
would you consider in this design?

2. If table A, column X has n rows with value J and table B, column Y has m rows with value J,
then when we join A and B on X and Y, how many rows will the value J appear in, assuming
both m and n are greater than zero?

Your answer should include the following pieces:
- Executable piece of code that does Question 1
- Write-up on Question 2, 3 and short questions