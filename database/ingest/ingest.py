import csv
import json
import os
import os.path
import time
import requests
import urllib.request
import gzip
import shutil
import datetime
import re
import codecs

import dotenv
import psycopg2
import psycopg2.errors
from shapely.geometry import shape

def create_database():
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = '{}'".format(os.environ['DB_NAME']))
    exists = cursor.fetchone()
    if not exists:
        cursor.execute('CREATE DATABASE {}'.format(os.environ['DB_NAME']))
        cursor.execute('CREATE EXTENSION postgis')


def download_datasets():

    path = 'datasets'

    if not os.path.exists(path):
        os.mkdir(path)

    flights_path = download_flight_data(path)
    print('Downloaded flight data to {}'.format(flights_path))

    covid_path = download_covid_data(path)
    print('Downloaded covid case data to {}'.format(covid_path))

    regions_path = download_regions_data(path)
    print('Downloaded regions data to {}'.format(regions_path))

    airport_path = download_airport_data(path)
    print('Downloaded airport data to {}'.format(airport_path))

    return {
        'airports': airport_path,
        'regions': regions_path,
        'covid': covid_path,
        'flights': flights_path
    }


def download_flight_data(path):
    flight_path = 'datasets/flights'
    if not os.path.exists(flight_path):
        os.mkdir(flight_path)
    record_id = 6325961
    record = requests.get('https://zenodo.org/api/records/%s' % record_id, params={'access_token': os.environ['ZENODO_KEY']})
    max_entries = 24
    entries = 0
    for file_record in json.loads(record.content.decode('utf-8'))['files']:
        file_path = os.path.join(flight_path, file_record['key'])
        if not os.path.exists(file_path[:-3]):
            urllib.request.urlretrieve(file_record['links']['self'], file_path)
            with gzip.open(file_path, 'rb') as zip_file:
                with open(file_path[:-3], 'wb') as csv_file:
                    shutil.copyfileobj(zip_file, csv_file)
            os.remove(file_path)
        entries += 1
        if entries >= max_entries:  # only want the 24 first months in the dataset
            break
    return flight_path


def download_regions_data(path):

    regions_filename = 'regions.geojson'
    regions_path = os.path.join(path, regions_filename)

    if not os.path.exists(regions_path):
        regions_url = 'https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_01M_2016_4326.geojson'
        urllib.request.urlretrieve(regions_url, regions_path)

    return regions_path


def download_covid_data(path):

    covid_filename = 'covid_cases.csv'
    covid_path = os.path.join(path, covid_filename)

    if not os.path.exists(covid_path):
        covid_url = 'https://opendata.ecdc.europa.eu/covid19/subnationalcasedaily/csv/data.csv'
        urllib.request.urlretrieve(covid_url, covid_path)

    return covid_path


def download_airport_data(path):

    airport_filename = 'airports.csv'
    airport_path = os.path.join(path, airport_filename)

    if not os.path.exists(airport_path):
        covid_url = 'http://ourairports.com/data/airports.csv'
        urllib.request.urlretrieve(covid_url, airport_path)

    return airport_path


def create_tables(cursor):

    create_db_sql = '''
    DROP TABLE IF EXISTS airports CASCADE;
    CREATE TABLE airports (
        code VARCHAR(25) PRIMARY KEY,
        name VARCHAR(128),
        type VARCHAR(32),
        elevation REAL,
        continent VARCHAR(2),
        location GEOGRAPHY(POINT)
    );

    DROP TABLE IF EXISTS imported_flights CASCADE;
    CREATE TABLE imported_flights (
        id SERIAL PRIMARY KEY,
        callsign VARCHAR(8),
        number VARCHAR(10),
        icao24 VARCHAR(10),
        registration VARCHAR(30),
        typecode VARCHAR(50),
        origin VARCHAR(25),
        destination VARCHAR(25),
        firstseen TIMESTAMP,
        lastseen TIMESTAMP,
        "day" DATE,
        latitude_1 NUMERIC,
        longitude_1 NUMERIC,
        altitude_1 NUMERIC,
        latitude_2 NUMERIC,
        longitude_2 NUMERIC,
        altitude_2 NUMERIC
    );
    DROP TABLE IF EXISTS regions CASCADE;
    CREATE TABLE regions (
        id VARCHAR(5) PRIMARY KEY,
        level SMALLINT,
        geom GEOGRAPHY(MULTIPOLYGON),
        center_code VARCHAR(2),
        name VARCHAR(128),
        mount_type SMALLINT,
        urbn_type SMALLINT,
        coast_type SMALLINT
    );
    DROP TABLE IF EXISTS covid_cases CASCADE;
    CREATE TABLE covid_cases (
        id SERIAL PRIMARY KEY,
        region_id VARCHAR(5) REFERENCES regions(id),
        date DATE,
        incidence REAL
    );
    DROP TABLE IF EXISTS runtimes;
    CREATE TABLE runtimes (
        id SERIAL PRIMARY KEY,
        service VARCHAR (50),
        request VARCHAR(50),
        runtime NUMERIC,
        stamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    '''
    cursor.execute(create_db_sql, [])


def ingest(cursor, destinations, conn):
    #ingest_airports(cursor, destinations['airports'])
    print('Ingested airports')
    ingest_regions(cursor, destinations['regions'])
    print('Ingested regions')
    #ingest_flights(cursor, destinations['flights'], conn)
    print('Ingested flights')
    ingest_covid(cursor, destinations['covid'])
    print('Ingested covid cases')


def ingest_airports(cursor, path):

    sql = '''
    INSERT INTO airports (code, name, type, elevation, continent, location) VALUES
    (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326));
    '''

    with open(path, 'r') as csvfile:
        csvreader = csv.DictReader(csvfile)
        for line in csvreader:
            insert = [line['ident'], line['name'], line['type'], None, line['continent'], line['longitude_deg'], line['latitude_deg']]
            insert[3] = line['elevation_ft'] if line['elevation_ft'] else None
            cursor.execute(sql, insert)


def ingest_regions(cursor, path):

    sql = '''
    INSERT INTO regions (id, level, geom, center_code, name, mount_type, urbn_type, coast_type) VALUES
    (%s, %s, ST_Multi(ST_SetSRID(%s::geometry, 4326)), %s, %s, %s, %s, %s)
    '''

    with open(path, 'r') as geojsonfile:
        json_object = json.load(geojsonfile)
        for region in json_object['features']:
            geometry = shape(region['geometry'])
            insert = (region['properties']['NUTS_ID'], region['properties']['LEVL_CODE'], geometry.wkb_hex,
                      region['properties']['CNTR_CODE'], region['properties']['NAME_LATN'],
                      region['properties']['MOUNT_TYPE'], region['properties']['URBN_TYPE'],
                      region['properties']['COAST_TYPE'])
            cursor.execute(sql, insert)


def ingest_covid(cursor, path):
    sql = '''
        INSERT INTO covid_cases (region_id, incidence, date) 
        VALUES (%s, %s, %s);
        '''

    with codecs.open(path, 'r', encoding='ISO-8859-2') as csvfile:
        csvreader = csv.DictReader(csvfile)
        for line in csvreader:
            if re.match('[0-9]{4}-[0-9]{2}-[0-9]{2}', line['date']):
                date = datetime.datetime.strptime(line['date'], '%Y-%m-%d').date()
            else:
                date = datetime.datetime.strptime(line['date'], '%Y%m%d').date()
            insert = [line['nuts_code'], None, date]
            insert[1] = line['rate_14_day_per_100k'] if line['rate_14_day_per_100k'] and line['rate_14_day_per_100k'] != 'NA' else None
            cursor.execute(sql, insert)


def ingest_flights(cursor, path, conn):
    filenames = sorted(os.listdir(path))
    for filename in filenames:
        with open(os.path.join(path, filename), 'rb') as fd:
            cursor.copy_expert('COPY imported_flights (callsign, number, icao24, registration, typecode, origin, destination, firstseen, lastseen, day, latitude_1, longitude_1, altitude_1, latitude_2, longitude_2, altitude_2) FROM stdin CSV HEADER DELIMITER AS \',\'', fd)
        conn.commit()
        print('Finished file {}'.format(filename))
    sql = '''
    create table flights as select id, callsign, number, registration, origin, destination, firstseen, lastseen from imported_flights tablesample bernoulli (10);
    '''
    cursor.execute(sql)
    drop_sql = '''
    DROP TABLE imported_flights
    '''
    cursor.execute(drop_sql)


if __name__ == '__main__':

    dotenv.load_dotenv('../.env')
    retries = 0
    max_retries = 5
    connected = False
    while retries < max_retries and not connected:
        try:
            conn = psycopg2.connect(user=os.environ['DB_USER'], password=os.environ['DB_PASS'], host=os.environ['DB_HOST'],
                                    port=os.environ['DB_PORT'])
            conn.set_session(autocommit=True)
            create_database()
            conn = psycopg2.connect(user=os.environ['DB_USER'], password=os.environ['DB_PASS'], host=os.environ['DB_HOST'],
                                    port=os.environ['DB_PORT'], dbname=os.environ['DB_NAME'])
            connected = True
        except psycopg2.OperationalError:
            retries += 1
            time.sleep(5)
            if retries == max_retries:
                raise EnvironmentError('Database connection failed')


    cursor = conn.cursor()

    create_tables(cursor)
    conn.commit()

    paths = download_datasets()

    ingest(cursor, paths, conn)

    conn.commit()
    conn.close()
