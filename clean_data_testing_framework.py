#!/usr/bin/env python

# Question 2 :

# In production, we deal with data at scale ingested from various data sources. 
# How would you go about designing a testing framework to ensure that this data is clean after being processed?

# Please share a plan that is as granular and operation-able as possible.
# Feel free to start high- level, zoom into one data field, 
# e.g. NDC, and flesh it out to show how you would design it, using it as an example.

from de_dupe_ndc import make_set, format_ndc_field, write_set
from textwrap import dedent
import pytest


class Testdedupe:
    """
    test each function does what we expect it to do.
    make_set should return no duplicates for example.
    format_ndc_field should return the same dataset but with cleaned up ndc field.
    ideally, we would like to clean each field.
    write set should write exactly as we expect, just like the original except for the cleaned up field.

    For example, for format_ndc_field, we would want to test that an ndc with less than 11 chars gets leading zeroes
    added in front of it. Likewise, we'd want another test to see that it removes `-` characters as well.
    If there are additional chars to omit from ndc, we may want to store it in a data structure to be picked up
    by the function.

    Other things I'd do is enforce the format of the payments fields of which there are two fields. 
    The assumption this is in USD or native is something that should likely be normalized if it's not.
    We should also know whether we want to see fields as nullable or not.

    The laundry list for identifying these cases is a long one. These are examples of perhaps of where I'd start, 
    in terms of brainstorming on this task. And based on demands from the business side, these can be prioritized.

    One of the things I am already doing is enforcing types in de_dupe_ndc.py for input and output
    We want to know that the types we send are what we get back out.
    """
    def test_no_dupes_in_make_set(self):
        test_set = dedent(
            """
            '1', '2', '3', '4', '5'
            '1', '2', '3', '4', '5'
            'a', 'b', 'c', 'd', 'e'
            """
        )
        actual_output = make_set(test_set)
        expected_output = {('1', '2', '3', '4', '5'), ('a', 'b', 'c', 'd', 'e')}
        assert expected_output == actual_output

if __name__ == "__main__":
    pytest.main()