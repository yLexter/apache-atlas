from ..utils.API import HTTPMethod, API
from ..utils.Exception import AtlasServiceException
from ..utils.Types import *
from apache_atlas.client.ApacheAtlas import ApacheAtlasClient
from ..utils.Constants import TypeNames, EndRelations
import json
import pandas as pd

class LineageClient:

    LINEAGE_BY_GUID = API(
        path="/lineage/{guid}",
        method=HTTPMethod.GET
    )

    def __init__(self, client: ApacheAtlasClient):
        self.client = client

    def get_data_lineage(self, entity_guid):
        entity = self.client.entity.get_entity_by_guid(entity_guid)

        lineage_column = self.client.lineage.get_lineage_by_guid(entity['entity']['guid'])
        last_entity_guid = self.client.lineage.get_last_guid_entity_of_lineage(lineage_column['relations'])
            
        last_entity = None

        if last_entity_guid:
            last_entity = self.client.entity.get_entity_by_guid(last_entity_guid)
        else:
            last_entity = entity

        return {
            'lineage': lineage_column,
            'last_entity': last_entity,
            'total_process': self.client.utils.get_version_lineage(len(lineage_column['relations'])),
            'number_process': len(lineage_column['relations'])
        }

    def get_lineage_by_guid(self, guid_entity):
        return self.client.request(
            self.LINEAGE_BY_GUID
                .format_path({ "guid": guid_entity })
                .add_query_params({ "depth": 999_999 })
        )

    def get_last_guid_entity_of_lineage(self, data):
        
        if not data:
            return None
        
        from_entity_ids = { item["fromEntityId"] for item in data }
        to_entity_ids = { item["toEntityId"] for item in data }

        unique_to_entity = to_entity_ids - from_entity_ids

        if not unique_to_entity:
            return None

        return unique_to_entity.pop()
    
    def create_lineage_table(self, data, table_acronymus: str):
        table = self.client.search.search_table_by_acronymus(table_acronymus)

        if not table:
            raise AtlasServiceException("Tabela não existe")

        full_entity_table = self.client.entity.get_entity_by_guid(table['guid'])

        list_guids_columns = [column['guid'] for column in full_entity_table['entity']['relationshipAttributes']['columns_table']]        
        guid_columns = {
            full_entity_table['referredEntities'][guid]['attributes']['name']: guid for guid in list_guids_columns
        }

        table_name = full_entity_table['entity']['attributes']['name']
        entities_lineage = []

        #Só aceitar no formato TTYYMM onde TT é a sigla da tabela, YY o ano e MM o mes
        for lineage, columns in data.items():
            year = lineage[-4:-2]
            month = lineage[-2:]

            columns = list(map(lambda x: x.strip(), columns))
            columns.sort()
            
            columns_guid = [{ "guid": guid_columns[column] } for column in columns]
            int_year = int(year)

            entities_lineage.append({
                "typeName": f"{TypeNames.MONTLY_TABLE}",
                "attributes": {
                    'name': f"{lineage}",
                    'description': f'Colunas das Tabelas de {table_acronymus} do ano {year} e mês {month}',
                    "qualifiedName": f"{TypeNames.MONTLY_TABLE}.DataSUS.{table_acronymus}@{lineage}",
                    # Chegar em 2080 esse codigo quebra
                    'year': 1900 + int_year if int_year > 80 and int_year <= 99 else 2000 + int_year,
                    "month": month,
                    EndRelations.END_LINEAGE_TO_COLUMN[0]: columns_guid,
                    EndRelations.END_TABLE_TO_COLUMNS_TIME[1]: {
                        'guid': table['guid']                         
                    }
                }
            })

        entities_lineage = self.client.entity.create_multiple_entities(entities_lineage)

        entity_timeline = {
            "typeName": f"{TypeNames.TIMELINE}",
            "attributes": {
                "name": f"Timeline de {table_name}",
                "description": f'Timeline de alteração de colunas do datasus de {table_name}',
                'qualifiedName': f"Process.DataSUS.{TypeNames.TIMELINE}@{table_name}"
            }
        }

        entity_timeline = self.client.entity.create_entity(entity_timeline)    
        lineage = self.client.utils.detect_column_changes(data)

        process_timeline = []

        for entity in lineage:
            lineage_string = entity['interval']
            start, end = lineage_string.split('-')
        
            addedColumns = entity['added']
            deletedColumns = entity['removed']

            entity_start = self.client.utils.find(
                lambda entity: entity['attributes']['name'].strip().lower() == start.strip().lower(), 
                entities_lineage
            )

            entity_end = self.client.utils.find(
                lambda entity: entity['attributes']['name'].strip().lower() == end.strip().lower(), 
                entities_lineage
            )

            process_timeline.append({
                "typeName": f"{TypeNames.PROCESS_CHANGE_COLUMN}",
                "attributes": {
                    "name": f"Alteracão de Colunas | {start} - {end}",
                    "description": f"Alteração de Colunas na tabela de {start} - {end}",
                    "qualifiedName": f"process.{TypeNames.PROCESS_CHANGE_COLUMN}.DataSUS.{table_acronymus}@{lineage_string}",
                    'added_columns': [{ "guid": guid_columns[column] } for column in addedColumns],
                    'deleted_columns': [{ "guid": guid_columns[column] } for column in deletedColumns],
                    "inputs": [
                        {
                            "typeName": entity_start['typeName'],
                            "guid": entity_start['guid']  
                        },
                    ],
                    "outputs": [
                        {
                            "typeName": entity_end['typeName'],
                            "guid": entity_end['guid'],  
                        },
                    ],
                    "processType": "ETL",
                    EndRelations.END_TIMELINE_TO_TABLE[1] : {
                        'guid': entity_timeline['guid']
                    }
                }
            })
        
        return self.client.entity.create_multiple_entities(process_timeline)
    
    def create_entity_lineage_by_interval_time_monthly(self, interval, table_acronymus, id_process):
        table = self.client.search.search_table_by_acronymus(table_acronymus)

        if not table:
            raise AtlasServiceException("Tabela não existe")
        
        querys = [
            f'/search/dsl?query={TypeNames.MONTLY_TABLE}',
            'name like "{acronymus}*"',
            'and ((year > {start_year}) or (year = {start_year} and month >= {start_month}))',
            'and ((year < {end_year}) or (year = {end_year} and month <= {end_month}))'
        ]

        start_year, end_year, start_month, end_month = interval['start_year'], interval['end_year'], interval['start_month'], interval['end_month']
            
        query = ' '.join(querys).format(**{
            'acronymus': table_acronymus,
            'start_year': start_year,
            'end_year': end_year,
            'start_month': start_month,
            'end_month': end_month,
        })
        
        response = self.client.request(API(query, HTTPMethod.GET))

        entities_monthly_tables = response['entities']
        guids_entities_month = [entitity['guid'] for entitity in entities_monthly_tables]

        full_entities_monthy = self.client.entity.get_entities_by_guid(guids_entities_month)

        columns = set()
        files = set()

        for full_entity_month in full_entities_monthy['entities']:
            columns_entity = full_entity_month['relationshipAttributes'][EndRelations.END_LINEAGE_TO_COLUMN[0]]
            files_entity = full_entity_month['relationshipAttributes'][EndRelations.END_TABLE_FILE_COLUMN[1]]

            for column in columns_entity:
                columns.add(column['guid'])

            for file in files_entity:
                files.add(file['guid'])

        start_month_string = "{:02}".format(int(start_month))
        end_month_string = "{:02}".format(int(end_month))

        entity_body = {
            'typeName': TypeNames.DATASET_PROCESSING_LINEAGE,
            'attributes': {
                'name': f'Arquivos de {table_acronymus} - {start_month_string}{start_year}-{end_month_string}{end_year}',
                'qualifiedName': f'{TypeNames.DATASET_PROCESSING_LINEAGE}.{table_acronymus}@{id_process}',
                'description': f"Arquivos de {table_acronymus} que passaram por um processo",
                'files_interval': [ { 'guid': guid_file } for guid_file in files],
                'columns': [{ 'guid': column_guid } for column_guid in columns],
                'id': id_process
            }
        }

        return self.client.entity.create_entity(entity_body)
    
    def create_entity_lineage_by_interval_time_anual(self, interval, table_acronymus, process_attributes):
        
        if 'id_process' not in process_attributes:
            raise AtlasServiceException("ID do ETL Batch é obrigatório") 
        
        table = self.client.search.search_table_by_acronymus(table_acronymus)

        if not table:
            raise AtlasServiceException("Tabela não existe")
        
        querys = [
            f'/search/dsl?query={TypeNames.ANUAL_TABLE}',
            'name like "{acronymus}*"',
            'and (year >= {start_year} and year <= {end_year})',
        ]

        start_year, end_year,  = interval['start_year'], interval['end_year']
            
        query = ' '.join(querys).format(**{
            'acronymus': table_acronymus,
            'start_year': start_year,
            'end_year': end_year,
        })
        
        response = self.client.request(API(query, HTTPMethod.GET))

        entities_monthly_tables = response['entities']
        guids_entities_month = [entitity['guid'] for entitity in entities_monthly_tables]

        entities_anual_columns = self.client.entity.get_entities_by_guid(guids_entities_month)

        columns = set()
        files = set()

        for entity_anual_column in entities_anual_columns['entities']:
            columns_entity = entity_anual_column['relationshipAttributes'][EndRelations.END_LINEAGE_TO_COLUMN[0]]
            files_entity = entity_anual_column['relationshipAttributes'][EndRelations.END_TABLE_FILE_COLUMN[1]]

            for column in columns_entity:
                columns.add(column['guid'])

            for file in files_entity:
                files.add(file['guid'])

        if 'name' not in process_attributes and 'description' not in process_attributes:
            process_attributes = {
                'name': f'Arquivos de {table_acronymus} - {start_year}-{end_year}',
                'description': f"Arquivos de {table_acronymus} que passaram por um processo dos anos de {start_year} até {end_year}",
            }
        
        id_process = process_attributes['id_process']

        entity_body = {
            'typeName': TypeNames.DATASET_PROCESSING_LINEAGE,
            'attributes': {
                 ** process_attributes,
                 ** {
                    'qualifiedName': f'{TypeNames.DATASET_PROCESSING_LINEAGE}.{table_acronymus}@{id_process}',
                    'files_interval': [ { 'guid': guid_file } for guid_file in files],
                    'table': { 'guid': table['guid'] },
                    'columns': [{ 'guid': column_guid } for column_guid in columns],
                    'id': id_process     
                 }
            }
        }

        return self.client.entity.create_entity(entity_body)



