#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple Apple Health XML to CSV
==============================
:File: convert.py
:Description: Convert Apple Health "export.xml" file into a csv
:Version: 0.0.2
:Created: 2019-10-04
:Updated: 2023-10-29
:Authors: Jason Meno (jam)
:Dependencies: An export.xml file from Apple Health
:License: BSD-2-Clause
"""

# %% Imports
import os
import pandas as pd
import xml.etree.ElementTree as ET
import datetime as dt
import sys


# %% Function Definitions

def preprocess_to_temp_file(file_path):
    """
    The export.xml file is where all your data is, but Apple Health Export has
    two main problems that make it difficult to parse: 
        1. The DTD markup syntax is exported incorrectly by Apple Health for some data types.
        2. The invisible character \x0b (sometimes rendered as U+000b) likes to destroy trees. Think of the trees!

    Knowing this, we can save the trees and pre-processes the XML data to avoid destruction and ParseErrors.
    """

    print("Pre-processing and writing to temporary file...", end="")
    sys.stdout.flush()

    temp_file_path = "temp_preprocessed_export.xml"
    with open(file_path, 'r', encoding='UTF-8') as infile, open(temp_file_path, 'w', encoding='UTF-8') as outfile:
        skip_dtd = False
        for line in infile:
            if '<!DOCTYPE' in line:
                skip_dtd = True
            if not skip_dtd:
                line = strip_invisible_character(line)
                outfile.write(line)
            if ']>' in line:
                skip_dtd = False

    print("done!")
    return temp_file_path

def strip_invisible_character(line):
    return line.replace("\x0b", "")


def format_workout_extras(workout_row):
    details = []

    if pd.notna(workout_row.get('duration')):
        unit = workout_row.get('durationUnit', 'min')
        details.append(f"{workout_row['duration']:.0f} {unit}")

    energy = workout_row.get('totalEnergyBurned')
    if pd.notna(energy):
        unit = workout_row.get('totalEnergyBurnedUnit', 'kcal')
        details.append(f"{energy:.0f} {unit}")

    distance = workout_row.get('totalDistance')
    if pd.notna(distance):
        unit = workout_row.get('totalDistanceUnit', 'km')
        details.append(f"{distance:.2f} {unit}")

    return " (" + ", ".join(details) + ")" if details else ""


def xml_to_csv(file_path):
    """Parses the XML file and returns a daily summary for weight-loss tracking."""

    print("Converting XML File to CSV...", end="")
    sys.stdout.flush()

    record_rows = []
    workout_rows = []
    target_record_types = {
        'HKQuantityTypeIdentifierBodyMass',
        'HKQuantityTypeIdentifierBodyFatPercentage',
        'HKCategoryTypeIdentifierSleepAnalysis'
    }

    for event, elem in ET.iterparse(file_path, events=('end',)):
        if event == 'end':
            if elem.tag == 'Record':
                record_type = elem.attrib.get('type')
                if record_type in target_record_types:
                    record_rows.append({
                        'type': record_type,
                        'value': elem.attrib.get('value'),
                        'unit': elem.attrib.get('unit'),
                        'startDate': elem.attrib.get('startDate'),
                        'endDate': elem.attrib.get('endDate')
                    })
            elif elem.tag == 'Workout':
                workout_rows.append({
                    'activityType': elem.attrib.get('workoutActivityType'),
                    'startDate': elem.attrib.get('startDate'),
                    'endDate': elem.attrib.get('endDate'),
                    'duration': elem.attrib.get('duration'),
                    'durationUnit': elem.attrib.get('durationUnit'),
                    'totalEnergyBurned': elem.attrib.get('totalEnergyBurned'),
                    'totalEnergyBurnedUnit': elem.attrib.get('totalEnergyBurnedUnit'),
                    'totalDistance': elem.attrib.get('totalDistance'),
                    'totalDistanceUnit': elem.attrib.get('totalDistanceUnit')
                })

            # Clear the element from memory to avoid excessive memory consumption
            elem.clear()

    records_df = pd.DataFrame(record_rows)
    workouts_df = pd.DataFrame(workout_rows)

    if not records_df.empty:
        records_df['startDate'] = pd.to_datetime(records_df['startDate'])
        records_df['endDate'] = pd.to_datetime(records_df['endDate'])
        records_df['date'] = records_df['startDate'].dt.date
        records_df['type'] = records_df['type'].str.replace('HKQuantityTypeIdentifier', "")
        records_df['type'] = records_df['type'].str.replace('HKCategoryTypeIdentifier', "")
        records_df = records_df.drop_duplicates(
            subset=['type', 'date', 'value'],
            keep='first'
        )

    if not workouts_df.empty:
        workouts_df['startDate'] = pd.to_datetime(workouts_df['startDate'])
        workouts_df['endDate'] = pd.to_datetime(workouts_df['endDate'])
        workouts_df['date'] = workouts_df['startDate'].dt.date
        workouts_df['activity'] = workouts_df['activityType'].str.replace(
            'HKWorkoutActivityType', "")
        numeric_cols = ['duration', 'totalEnergyBurned', 'totalDistance']
        workouts_df[numeric_cols] = workouts_df[numeric_cols].apply(
            pd.to_numeric, errors='coerce')
        workouts_df['workout_details'] = workouts_df.apply(
            lambda row: f"{row['startDate'].strftime('%H:%M')}-"
                        f"{row['endDate'].strftime('%H:%M')} {row['activity']}"
                        f"{format_workout_extras(row)}",
            axis=1)

    body_weight = pd.Series(dtype='float64')
    body_fat = pd.Series(dtype='float64')
    sleep_hours = pd.Series(dtype='float64')
    workouts_by_day = pd.Series(dtype='object')

    if not records_df.empty:
        body_weight = (
            records_df[records_df['type'] == 'BodyMass']
            .assign(value=lambda df: pd.to_numeric(df['value'], errors='coerce'))
            .groupby('date')['value']
            .mean()
        )

        body_fat = (
            records_df[records_df['type'] == 'BodyFatPercentage']
            .assign(value=lambda df: pd.to_numeric(df['value'], errors='coerce'))
            .groupby('date')['value']
            .mean()
        )

        sleep_records = records_df[records_df['type'] == 'SleepAnalysis'].copy()
        if not sleep_records.empty:
            sleep_records['is_asleep'] = sleep_records['value'].str.contains(
                'Asleep', na=False)
            sleep_records = sleep_records[sleep_records['is_asleep']]
            sleep_records['sleep_minutes'] = (
                sleep_records['endDate'] - sleep_records['startDate']
            ).dt.total_seconds() / 60
            sleep_hours = (
                sleep_records.groupby('date')['sleep_minutes'].sum() / 60
            )

    if not workouts_df.empty:
        workouts_by_day = (
            workouts_df.groupby('date')['workout_details']
            .apply(lambda values: " | ".join(values))
        )

    combined = pd.concat(
        [
            body_weight.rename('body_weight'),
            body_fat.rename('body_fat'),
            sleep_hours.rename('sleep_hours'),
            workouts_by_day.rename('workouts')
        ],
        axis=1
    ).reset_index().rename(columns={'index': 'date'})

    if not combined.empty:
        combined['date'] = pd.to_datetime(combined['date'])
        combined = combined.set_index('date').sort_index()
        full_index = pd.date_range(
            start=combined.index.min(),
            end=combined.index.max(),
            freq='D'
        )
        combined = combined.reindex(full_index)
        combined[['body_weight', 'body_fat']] = combined[
            ['body_weight', 'body_fat']
        ].interpolate(method='time', limit_area='inside')
        combined.index.name = 'date'
        combined = combined.reset_index()
        combined['date'] = combined['date'].dt.date

    combined.sort_values(by='date', ascending=False, inplace=True)

    print("done!")

    return combined


def load_strong_workouts(file_path="strong_workouts.csv"):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Missing Strong workouts file: {file_path}")

    strong_workouts_df = pd.read_csv(file_path)
    dedupe_columns = [
        'Date',
        'Workout Name',
        'Exercise Name',
        'Set Order',
        'Weight',
        'Reps',
        'Distance',
        'Seconds'
    ]
    available_columns = [
        column for column in dedupe_columns if column in strong_workouts_df.columns
    ]
    if available_columns:
        strong_workouts_df = strong_workouts_df.drop_duplicates(
            subset=available_columns,
            keep='first'
        )

    return strong_workouts_df


def save_to_csv(health_df):
    print("Saving CSV file...", end="")
    sys.stdout.flush()

    today = dt.datetime.now().strftime('%Y-%m-%d')
    health_df.to_csv("apple_health_export_" + today + ".csv", index=False)
    print("done!")

    return

def remove_temp_file(temp_file_path):
    print("Removing temporary file...", end="")
    os.remove(temp_file_path)
    print("done!")
    
    return

def main():
    file_path = "export.xml"
    temp_file_path = preprocess_to_temp_file(file_path)
    health_df = xml_to_csv(temp_file_path)
    save_to_csv(health_df)
    remove_temp_file(temp_file_path)

    return


# %%
if __name__ == '__main__':
    main()
