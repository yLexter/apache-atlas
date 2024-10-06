from ..utils.API import HTTPMethod, API
from ..utils.Exception import AtlasServiceException
from ..utils.Types import *
from apache_atlas.client.ApacheAtlas import ApacheAtlasClient
import json
import pandas as pd

class EntityClient:

    ENTITY_API = "/entity/"

    BULK_ENTITY = API(ENTITY_API + "bulk", HTTPMethod.POST)
    CREATE_ENTITY = API(ENTITY_API, HTTPMethod.POST)
    GET_ENTITY = API(ENTITY_API + "guid/{guid}", HTTPMethod.GET)
    DELETE_ENTITY = API(ENTITY_API + "guid/{guid}", HTTPMethod.DELETE)

    def __init__(self, client: ApacheAtlasClient):
        self.client = client

    def delete_entity_by_guid(self, guid_entity):
        return self.client.request(
             api_instance=self.DELETE_ENTITY.format_path({ 'guid': guid_entity })
        )

    def create_entity(self, entity):
        entity_body = { 
            "entity": entity
        }

        response = self.client.request(
            self.CREATE_ENTITY,
            entity_body
        )

        if 'mutatedEntities' not in response:
            return response

        if 'CREATE' in response['mutatedEntities']:
            return response['mutatedEntities']['CREATE'][0]

        return response
    
    def create_multiple_entities(self, entities):
        body = {
            "entities": entities
        }

        response = self.client.request(
            self.BULK_ENTITY,
            body
        )

        return response

    def create_entity_file_table(self, data: FileDO, table_acronymus: str, table_column: str):
        file_exists = self.client.search.search_by_attribute(   
            attributes={
                'typeName': 'dt_table_file',
                'attrName': 'name',
                'attrValuePrefix': data['name'],
                'limit': 1,
                'offset': 0
            }
        )

        if 'entities' in file_exists:
            raise AtlasServiceException("Esse Arquivo já possui metadados")

        entity_table = self.client.search.search_table_by_acronymus(table_acronymus.upper())

        if not entity_table:
            raise AtlasServiceException("Essa tabela não existe, ou sigla está errada")

        entity_column = self.client.search.search_annual_table(table_column)

        if not entity_column:
            raise AtlasServiceException(f"Esse ano ({data['table_column']}) não possui uma entidade ou nome está inválido")
        
        entity_file = {
            'typeName': 'dt_table_file',
            'attributes': {
                **data,
                ** {
                    'qualifiedName': f'dt_table_file.DataSUS@{data["name"]}',
                    'is_file_table': {
                        'guid': entity_table['guid']
                    },
                    'columns_file_table': {
                        'guid': entity_column['guid']
                    }   
                  }
             },
        }

        return self.client.entity.create_entity(entity_file)

    def create_entity_dt_table(self, attributes: AttributesTable, database_acronymus: str):
       database = self.client.search.search_unique_entity({
           'typeName': 'dt_database',
           'attrName': 'acronymus',
           'attrValue': database_acronymus
       })

       if not database:
           raise AtlasServiceException("Database nào existe")
       
       attributes['qualifiedName'] = f"dt_table.DataSUS@{attributes['acronymus']}"
       attributes['acronymus'] = attributes['acronymus'].upper()
       attributes['belongs_database'] = {
           "guid": database['guid']
       }

       entity_body = {
           "typeName": "dt_table",
           "attributes": attributes
       }

       return self.client.entity.create_entity(entity_body)

    def get_entity_by_guid(self, guid_entity):
        return self.client.request(
             api_instance=self.GET_ENTITY.format_path({ 'guid': guid_entity })
        )
    
    def create_entity_columns(self, path_csv, table_acronymus: str):
        table = self.client.search.search_table_by_acronymus(table_acronymus)

        if not table:
            raise AtlasServiceException("Tabela não existe")
        
        columns = []

        df_columns = pd.read_csv(path_csv)
        df_columns.fillna('', inplace=True)

        for _, row in df_columns.iterrows():
            columns.append({
                "typeName": "dt_table_column",
                "attributes": {
                    "name": row['name'],
                    "qualifiedName": f"dt_table_column.DataSUS.{table_acronymus}@{row['name']}",
                    "description": row['description'] if 'description' in row else "Não documentado...",
                    'primary_key': row['primary_key'] if 'primary_key' in row else False,
                    "domain": row['domain'] if 'domain' in row else "",
                    "type": row['type'] if 'type' in row else "",
                    'observation': row['observation'] if 'observation' in row else "",
                    "belongs_to_table" : {
                        'guid': table['guid']
                    }
                }
            })
        
        return self.create_multiple_entities(columns)

    def create_lineage_table(self, data, table_acronymus: str):
        table = self.client.search.search_table_by_acronymus(table_acronymus)

        if not table:
            raise AtlasServiceException("Tabela não existe")

        full_entity_table = self.get_entity_by_guid(table['guid'])

        list_guids_columns = [column['guid'] for column in full_entity_table['entity']['relationshipAttributes']['columns_table']]        
        guid_columns = {
            full_entity_table['referredEntities'][guid]['attributes']['name']: guid for guid in list_guids_columns
        }

        entities_lineage = []

        for lineage, columns in data.items():
            year = lineage[2:4]
            month = lineage[4:]

            columns.sort()
            columns_guid = [{ "guid": guid_columns[column] } for column in columns]

            entities_lineage.append({
                "typeName": "dt_monthly_table",
                "qualifiedName": f"dt_monthly_table@{lineage}",
                "attributes": {
                    'name': f"{lineage}",
                    'description': f'Colunas das Tabelas de {table_acronymus} do ano {year} e mês {month}',
                    "qualifiedName": f"dt_monthly_table@{lineage}",
                    'year': year,
                    "month": month,
                    "columns_anual_table": columns_guid,
                }
            })


        rsponse_entities_lineage = self.create_multiple_entities(entities_lineage)

        
        print(json.dumps(entities_lineage, indent=2))

    # todo ve ser essa ordenação ta certa
    def detect_column_changes(files):
        change_intervals = []
        
        sorted_files = sorted(files.keys())
        
        last_columns = None
        first_file = sorted_files[0]
        
        for i, file in enumerate(sorted_files):
            current_columns = set(files[file])
            
            if last_columns is None:
                last_columns = current_columns
                continue
            
            added_columns = current_columns - last_columns
            removed_columns = last_columns - current_columns
            
            if added_columns or removed_columns:
                interval = {
                    'interval': f"{first_file}-{file}".replace("AC", ""),
                    'added': list(added_columns),
                    'removed': list(removed_columns)
                }
            
                change_intervals.append(interval)
                first_file = file
            
            last_columns = current_columns
        
        return change_intervals