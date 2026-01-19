#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Combine Apple Health export.xml data with Strong workout exports.
"""

import datetime as dt
import os
import sys
import xml.etree.ElementTree as ET

import pandas as pd


# %% Function Definitions

def preprocess_to_temp_file(file_path):
    """
    The export.xml file is where all your data is, but Apple Health Export has
    two main problems that make it difficult to parse:
        1. The DTD markup syntax is exported incorrectly by Apple Health for some data types.
        2. The invisible character \x0b (sometimes rendered as U+000b) likes to destroy trees.

    Knowing this, we can save the trees and pre-process the XML data to avoid destruction and ParseErrors.
    """

    print("Pre-processing and writing to temporary file...", end="")
    sys.stdout.flush()

    temp_file_path = os.path.join("Data", "temp_preprocessed_export.xml")
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

    combined.sort_values(by='date', ascending=False, inplace=True)

    print("done!")

    return combined


def format_strong_set(row):
    weight = row.get('Weight', 0)
    reps = row.get('Reps', 0)
    distance = row.get('Distance', 0)
    seconds = row.get('Seconds', 0)
    rpe = row.get('RPE', 0)

    parts = []
    if weight or reps:
        if weight and reps:
            parts.append(f"{weight:g}x{reps:g}")
        elif weight:
            parts.append(f"{weight:g}")
        else:
            parts.append(f"{reps:g} reps")

    if distance:
        parts.append(f"{distance:g} distance")
    if seconds:
        parts.append(f"{seconds:g}s")
    if rpe:
        parts.append(f"RPE {rpe:g}")

    metrics = " ".join(parts)
    if metrics:
        return f"{row['Exercise Name']} {metrics}"
    return f"{row['Exercise Name']}"


def strong_workouts_summary(file_path):
    print("Loading Strong workouts...", end="")
    sys.stdout.flush()

    strong_df = pd.read_csv(file_path)
    strong_df['Date'] = pd.to_datetime(strong_df['Date'], dayfirst=True)
    strong_df['date'] = strong_df['Date'].dt.date

    strong_df = strong_df[strong_df['Set Order'] != 'Rest Timer']

    numeric_cols = ['Weight', 'Reps', 'Distance', 'Seconds', 'RPE']
    strong_df[numeric_cols] = strong_df[numeric_cols].apply(
        pd.to_numeric, errors='coerce').fillna(0)

    non_zero_mask = (strong_df[numeric_cols] != 0).any(axis=1)
    strong_df = strong_df[non_zero_mask]

    if strong_df.empty:
        print("done!")
        return pd.DataFrame(columns=['date', 'strong_summary'])

    strong_df['set_summary'] = strong_df.apply(format_strong_set, axis=1)

    workout_summary = (
        strong_df.groupby(['date', 'Workout Name'])['set_summary']
        .apply(lambda values: "; ".join(values))
        .reset_index()
    )

    workout_summary['workout_summary'] = (
        workout_summary['Workout Name']
        + ": "
        + workout_summary['set_summary']
    )

    daily_summary = (
        workout_summary.groupby('date')['workout_summary']
        .apply(lambda values: " | ".join(values))
        .reset_index()
        .rename(columns={'workout_summary': 'strong_summary'})
    )

    print("done!")

    return daily_summary


def merge_daily_summaries(health_df, strong_df):
    combined = health_df.merge(strong_df, on='date', how='outer')
    combined.sort_values(by='date', ascending=False, inplace=True)
    return combined


def save_to_csv(health_df):
    print("Saving CSV file...", end="")
    sys.stdout.flush()

    os.makedirs("Data", exist_ok=True)
    today = dt.datetime.now().strftime('%Y-%m-%d')
    output_path = os.path.join("Data", f"apple_health_export_{today}.csv")
    health_df.to_csv(output_path, index=False)
    print("done!")



def remove_temp_file(temp_file_path):
    print("Removing temporary file...", end="")
    os.remove(temp_file_path)
    print("done!")


def main():
    os.makedirs("Data", exist_ok=True)
    file_path = os.path.join("Data", "apple_health_export", "export.xml")
    strong_path = os.path.join("Data", "strong_workouts.csv")

    temp_file_path = preprocess_to_temp_file(file_path)
    health_df = xml_to_csv(temp_file_path)
    strong_df = strong_workouts_summary(strong_path)
    combined_df = merge_daily_summaries(health_df, strong_df)
    save_to_csv(combined_df)
    remove_temp_file(temp_file_path)


if __name__ == '__main__':
    main()
