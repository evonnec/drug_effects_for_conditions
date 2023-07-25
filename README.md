# Drug Effects For Conditions  
  
Health care company often deals with claims data, broken out into medical and pharmacy records,
to map out the longitudinal clinical history of patients. In this example use case, we are looking to identify the patients who have developed a certain condition `Y`, after taking a particular drug `X`. 

To allow for it, use the data set dummy_data.csv (attached):  
  
It has four data source files:  
● medical_data_sample  
● pharmacy_data_sample   
● ndc  
● diagnosis_code  
  
Medical and pharmacy are typically managed by separate entities, so that’s why the data often
comes in two pieces. The Medical data covers the billing information for doctor visits, surgeries, diagnostics, usually submitted by medical providers, and the pharmacy data covers the billing information of prescription drugs and therapies. For example, if a patient gets a prescription from CVS, this record would appear in the pharmacy claim data, as opposed to the medical.

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

Additionally, the ndc file provides the list of NDC codes for the Drug X
And the diagnosis_code file provides the list of diagnosis_code for the Condition Y

Question 1:

The claims data comes with duplicates and NDCs are messy. Please write a script to de-
deduplicate the data (Note: rows identical to each other in every single column is considered duplicate) and clean the NDC into standard format (11 digits and matchable to the format in tab ndc)

Remove common issues (unsaid: from pharmacy_data_sample.csv)
 - dashes between the numbers, 
 - and missing 0 on the front, among others. 

For the scope of this exercise, let’s consider these two as the only two issues.

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

2. If table A, column X has n rows with value J 
and 
table B, column Y has m rows with value J,

then when we join A and B on X and Y, how many rows will the value J appear in, assuming
both m and n are greater than zero?

Your answer should include the following pieces:
- Executable piece of code that does Question 1
- Write-up on Question 2, 3 and short questions

## Set Up
`python -m venv drugeffects`
`source drugeffects/bin/activate`
`pip install -e .`
  
## How to Run program for Q1:
`python src/drug_effects_for_conditions/de_dupe_ndc.py data/pharmacy_data_sample output/cleaned_pharm_data` where:
- `pharmacy_data_sample` is the csv filename that we are cleaning up NDC fields for  
and  
- `cleaned_pharm_data` is the output csv filename to write to.  

## How to Run tests for program:
`python -m pytest`

## Question 2:  
please find description of initial plan in `tests/clean_data_testing_framework.py`  
  
## Question 3:  
The script is generally optimized with enforced typing. One thing that could be done further is to perform the cleaning on a vector. Right now we iter thru each row, make the adjustment to the index position we want and add the other pieces back on. numpy is a library to consider for it. We can also perhaps use data structures that python provides if they are faster than numpy and simply change our code. Vector transformations are generally faster and make sense for large datasets.  
  
## Short Questions:  
1. I might consider a system in which we require product users to use their account in order to engage with the chatbot such that the name, email etc is consistent across data stores. Otherwise, we can perform a match on the user engaging with the chatbot and our list of product users. If not an exact match, we can use a classifier model in order to match them if it matters greatly. I would enforce it at ingestion to avoid having to deal with differing data that would remain disparate until an action is performed to unify them.  
  
2. It depends on what type of join we are using. if we are using a left join on A, n rows. if left join on B, m rows.
If we are doing an outer join in the event that either table has NULL for the J value that the other table has a value, it will be the set of (A's n rows union B's m rows). if it's inner join, we will only see the rows in which the J value is there for both A and B tables.  