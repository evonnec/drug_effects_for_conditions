
from csv import reader, writer
from typing import Set, Tuple, Any
from sys import argv

def make_set(
    input_file: str, 
    ) -> Set[Tuple]:
    with open(input_file, mode='r', newline='') as input_data:
        csvreader = reader(input_data, delimiter=',')
        output_set: Set[Tuple] = set()
        header = None
        for line in csvreader:
            if header is None: 
                header = tuple(line)
            else:
                (_, _, _, _, _) = tuple(line)
                output_set.add(tuple(line))
        return output_set

def format_ndc_field(dataset: Set[Tuple]) -> Set[Tuple]:
    output_set: Set[Tuple] = set()
    for row in dataset:
        list_row = list(row)
        res_str = ''.join([char for char in list_row[2] if char != '-'])
        new_ndc = res_str.zfill(11)
        output_set.add(tuple([list_row[0], list_row[1], new_ndc, list_row[3], list_row[4]]))
    return output_set

def write_set(
    output_file: str, 
    dataset: Set[Tuple]
    ) -> None:
    with open(output_file, mode='w', newline='') as output_data_file:
        _ = writer(output_data_file, delimiter=',')
        for line_list in dataset:
            line = ",".join([str(element) for element in line_list]) + "\n"
            output_data_file.write(line)

def dedupe_ndc_pharm_data(
    input_file: str, 
    output_file: str
    ) -> None:
    ndc_set = make_set(input_file)
    cleaned_ndc = format_ndc_field(ndc_set)
    write_set(output_file, cleaned_ndc)

if __name__ == "__main__":
    dedupe_ndc_pharm_data(
        input_file="./" + argv[1] + ".csv", 
        output_file="./" + argv[2] + ".csv",   
    )