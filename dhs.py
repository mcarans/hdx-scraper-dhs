#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
DHS:
-----

Generates HXlated API urls from the DHS website.

"""
import json
import logging
from os.path import join

from hdx.data.dataset import Dataset
from hdx.data.resource import Resource
from hdx.data.resource_view import ResourceView
from hdx.data.showcase import Showcase
from hdx.location.country import Country
from hdx.utilities.dictandlist import write_list_to_csv, dict_of_sets_add
from hdx.utilities.downloader import DownloadError
from slugify import slugify

logger = logging.getLogger(__name__)

description = 'Contains data from the [DHS data portal](https://api.dhsprogram.com/). There is also a dataset containing [%s](%s) on HDX.\n\nThe DHS Program Application Programming Interface (API) provides software developers access to aggregated indicator data from The Demographic and Health Surveys (DHS) Program. The API can be used to create various applications to help analyze, visualize, explore and disseminate data on population, health, HIV, and nutrition from more than 90 countries.'
hxltags = {'ISO3': '#country+code', 'Location': '#loc+name', 'DataId': '#meta+id', 'Indicator': '#indicator+name',
           'Value': '#indicator+value+num', 'Precision': '#indicator+precision', 'CountryName': '#country+name',
           'SurveyYear': '#date+year', 'SurveyId': '#survey+id', 'IndicatorId': '#indicator+code'}


def get_countries(base_url, downloader):
    url = '%scountries' % base_url
    response = downloader.download(url)
    json = response.json()
    countriesdata = list()
    for country in json['Data']:
        countryiso = country['UNSTAT_CountryCode']
        if countryiso:
            countriesdata.append({'iso3': countryiso, 'dhscode': country['DHS_CountryCode']})
    return countriesdata


def get_tags(base_url, downloader, dhscountrycode):
    url = '%stags/%s' % (base_url, dhscountrycode)
    response = downloader.download(url)
    json = response.json()
    return json['Data']


def get_publication(base_url, downloader, dhscountrycode):
    url = '%spublications/%s' % (base_url, dhscountrycode)
    response = downloader.download(url)
    json = response.json()
    publications = json['Data']
    publication = publications[0]
    for publicationdata in publications:
        if publication['SurveyType'] == 'DHS':
            if publicationdata['SurveyType'] != 'DHS':
                continue
            if publicationdata['SurveyYear'] == publication['SurveyYear']:
                if publicationdata['PublicationSize'] > publication['PublicationSize']:
                    publication = publicationdata
            elif publicationdata['SurveyYear'] > publication['SurveyYear']:
                publication = publicationdata
        else:
            if publicationdata['SurveyType'] == 'DHS':
                publication = publicationdata
            elif publicationdata['SurveyYear'] == publication['SurveyYear']:
                if publicationdata['PublicationSize'] > publication['PublicationSize']:
                    publication = publicationdata
            elif publicationdata['SurveyYear'] > publication['SurveyYear']:
                publication = publicationdata
    return publication


def get_dataset(countryiso, tags):
    dataset = Dataset()
    dataset.set_maintainer('196196be-6037-4488-8b71-d786adf4c081')
    dataset.set_organization('45e7c1a1-196f-40a5-a715-9d6e934a7f70')
    dataset.set_expected_update_frequency('Every year')
    dataset.add_country_location(countryiso)
    dataset.add_tags(tags)
    return dataset


def get_column_positions(headers):
    columnpositions = dict()
    for i, header in enumerate(headers):
        columnpositions[header] = i
    return columnpositions


def process_national_row(columnpositions, years, rows, row, countryiso):
    years.add(int(row[columnpositions['SurveyYear']]))
    row.insert(0, countryiso)
    rows.append(row)


def process_subnational_row(columnpositions, subyears, rows, row, countryiso):
    subyears.add(int(row[columnpositions['SurveyYear']]))
    val = row[columnpositions['CharacteristicLabel']]
    if val[:2] == '..':
        val = val[2:]
    row.insert(0, val)
    row.insert(0, countryiso)
    rows.append(row)


def set_dataset_date_bites(dataset, years, bites_disabled, national_subnational):
    years = sorted(list(years))
    latest_year = years[-1]
    dataset.set_dataset_year_range(years[0], latest_year)
    new_bites_disabled = [True, True, True]
    for i, indicator in enumerate(['CM_ECMR_C_IMR', 'HC_ELEC_H_ELC', 'ED_LITR_W_LIT']):
        indicator_latest_year = sorted(list(bites_disabled[national_subnational][indicator]))[-1]
        if indicator_latest_year == latest_year:
            new_bites_disabled[i] = False
    bites_disabled[national_subnational] = new_bites_disabled


def generate_datasets_and_showcase(configuration, base_url, downloader, folder, country, dhstags):
    """
    """
    countryiso = country['iso3']
    dhscountrycode = country['dhscode']
    countryname = Country.get_country_name_from_iso3(countryiso)
    title = '%s - Demographic and Health Data' % countryname
    logger.info('Creating datasets for %s' % title)
    tags = ['hxl', 'health', 'demographics']

    dataset = get_dataset(countryiso, tags)
    if dataset is None:
        return None, None, None, None
    dataset['title'] = title.replace('Demographic', 'National Demographic')
    slugified_name = slugify('DHS Data for %s' % countryname).lower()
    dataset['name'] = slugified_name
    dataset.set_subnational(False)

    subdataset = get_dataset(countryiso, tags)
    if dataset is None:
        return None, None, None, None

    subdataset['title'] = title.replace('Demographic', 'Subnational Demographic')
    subslugified_name = slugify('DHS Subnational Data for %s' % countryname).lower()
    subdataset['name'] = subslugified_name
    subdataset.set_subnational(True)

    dataset['notes'] = description % (subdataset['title'], configuration.get_dataset_url(subslugified_name))
    subdataset['notes'] = description % (dataset['title'], configuration.get_dataset_url(slugified_name))

    bites_disabled = {'national': dict(), 'subnational': dict()}

    years = set()
    subyears = set()
    for dhstag in dhstags:
        tagname = dhstag['TagName'].strip()
        resource_name = '%s Data for %s' % (tagname, countryname)
        resourcedata = {
            'name': resource_name,
            'description': 'HXLated csv containing %s data' % tagname
        }

        url = '%sdata/%s?tagids=%s&breakdown=national&perpage=10000&f=csv' % (base_url, dhscountrycode, dhstag['TagID'])
        generator = downloader.get_tabular_rows(url, format='csv')
        headers = next(generator)
        columnpositions = get_column_positions(headers)
        headers.insert(0, 'ISO3')
        rows = [headers, [hxltags.get(header, '') for header in headers]]
        if tagname == 'DHS Quickstats':
            for row in generator:
                indicatorid = row[columnpositions['IndicatorId']]
                if indicatorid in ['CM_ECMR_C_IMR', 'HC_ELEC_H_ELC', 'ED_LITR_W_LIT']:
                    dict_of_sets_add(bites_disabled['national'], indicatorid, int(row[columnpositions['SurveyYear']]))
                process_national_row(columnpositions, years, rows, row, countryiso)
        else:
            for row in generator:
                process_national_row(columnpositions, years, rows, row, countryiso)
        filepath = join(folder, '%s_national_%s.csv' % (tagname, countryiso))
        write_list_to_csv(rows, filepath)
        resource = Resource(resourcedata)
        resource.set_file_type('csv')
        resource.set_file_to_upload(filepath)
        dataset.add_update_resource(resource)

        url = url.replace('breakdown=national', 'breakdown=subnational')
        try:
            generator = downloader.get_tabular_rows(url, format='csv')
            headers = next(generator)
            columnpositions = get_column_positions(headers)

            headers.insert(0, 'Location')
            headers.insert(0, 'ISO3')
            rows = [headers, [hxltags.get(header, '') for header in headers]]
            if tagname == 'DHS Quickstats':
                for row in generator:
                    indicatorid = row[columnpositions['IndicatorId']]
                    if indicatorid in ['CM_ECMR_C_IMR', 'HC_ELEC_H_ELC', 'ED_LITR_W_LIT']:
                        dict_of_sets_add(bites_disabled['subnational'], indicatorid,
                                         int(row[columnpositions['SurveyYear']]))
                    process_subnational_row(columnpositions, subyears, rows, row, countryiso)
            else:
                for row in generator:
                    process_subnational_row(columnpositions, subyears, rows, row, countryiso)
            filepath = join(folder, '%s_subnational_%s.csv' % (tagname, countryiso))
            write_list_to_csv(rows, filepath)
            resource = Resource(resourcedata)
            resource.set_file_type('csv')
            resource.set_file_to_upload(filepath)
            subdataset.add_update_resource(resource)
        except DownloadError as ex:
            cause = ex.__cause__
            if cause is not None:
                if 'Variable RET is undefined' not in str(cause):
                    raise ex
            else:
                raise ex
    if len(dataset.get_resources()) == 0:
        dataset = None
    else:
        set_dataset_date_bites(dataset, years, bites_disabled, 'national')
    if len(subdataset.get_resources()) == 0:
        subdataset = None
    else:
        set_dataset_date_bites(subdataset, subyears, bites_disabled, 'subnational')

    publication = get_publication(base_url, downloader, dhscountrycode)
    showcase = Showcase({
        'name': '%s-showcase' % slugified_name,
        'title': publication['PublicationTitle'],
        'notes': publication['PublicationDescription'],
        'url': publication['PublicationURL'],
        'image_url': publication['ThumbnailURL']
    })
    showcase.add_tags(tags)
    return dataset, subdataset, showcase, bites_disabled


def generate_resource_view(dataset, quickchart_resourceno=0, bites_disabled=None):
    if bites_disabled == [True, True, True]:
        return None
    resourceview = ResourceView({'resource_id': dataset.get_resource(quickchart_resourceno)['id']})
    resourceview.update_from_yaml()
    hxl_preview_config = json.loads(resourceview['hxl_preview_config'])
    bites = hxl_preview_config['bites']
    if bites_disabled is not None:
        for i, disable in reversed(list(enumerate(bites_disabled))):
            if disable:
                del bites[i]
    for bite in bites:
        bite['type'] = 'key figure'
        bite['uiProperties']['postText'] = 'percent'
        del bite['ingredient']['aggregateColumn']
    resourceview['hxl_preview_config'] = json.dumps(hxl_preview_config)
    return resourceview
