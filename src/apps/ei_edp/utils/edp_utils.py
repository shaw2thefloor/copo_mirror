from common.utils.logger import Logger
from sapiopylib.rest.utils.recordmodel.PyRecordModel import PyRecordModel
from common.dal.profile_da import Profile
from .sapio.sapio_datamanager  import Sapio
from typing import List
import math
from src.apps.copo_single_cell_submission.utils.da import Singlecell, SinglecellSchemas
import pandas as pd
from src.apps.copo_core.models import User
from common.dal.copo_da import CopoGroup
from common.utils import helpers
import uuid
from .email import Email

l = Logger()

def get_sapio_sample_type_options():
    config = Sapio().picklistManager.get_picklist("Exemplar Sample Types")
    return [{"value": s, "label": s} for s in config.entry_list]
     
def pre_save_edp_profile(auto_fields, **kwargs):
    target_id = kwargs.get("target_id","")
    if not target_id:
        return {"status": "success"}
    profile = Profile().get_record(target_id)
    sapio_project_id = profile.get("sapio_project_id","")

    if sapio_project_id:            
        no_of_samples = auto_fields.get("copo.profile.no_of_samples", [])
        #plate_ids = auto_fields.get("copo.profile.sapio_plate_ids", "")
        project_records = Sapio().dataRecordManager.query_data_records(data_type_name="Project", 
                                                data_field_name="C_ProjectIdentifier", 
                                                value_list=[sapio_project_id]).result_list
        if not project_records or len(project_records) ==0:
            return {"status": "error", "message": f"Sapio Project {profile['sapio_project_id']} not found."}                
        project_record = project_records[0]
        project: PyRecordModel = Sapio().inst_man.add_existing_record(project_record)  
        Sapio().relationship_man.load_children([project], 'Sample')
        samples_under_project: List[PyRecordModel] = project.get_children_of_type('Sample')
        if samples_under_project:
            if len(samples_under_project) > int(no_of_samples):
                diff = len(samples_under_project) - int(profile["no_of_samples"])
                for sample in samples_under_project:
                    if not sample.get_field_value("C_CustomerSampleName"):
                        diff -=1

                    if diff <=0:
                        break
                if diff >0:
                    return {"status": "error", "message": f"Sapio Project {profile['sapio_project_id']} has customer samples associated. Cannot decrease the no. of samples."}

            """     
            plates = plate_ids.split(",")            
            assigned_plates = set()
            for sample in samples_under_project:
                assigned_plate = sample.get_field_value("PlateId")
                if assigned_plate:
                    assigned_plates.add(assigned_plate)
                    if assigned_plate not in plates:
                        return {"status": "error", "message": f"Sapio Project {profile['sapio_project_id']} has samples associated with plate {assigned_plate}. Cannot remove this plate from profile."}                    
            """
    """
    plates_str = auto_fields.get("copo.profile.sapio_plate_ids","").strip()
    if plates_str:
        plates = plates_str.split(",")  
        plates_records = Sapio().dataRecordManager.query_data_records(data_type_name="Plate", 
                                            data_field_name="PlateId", 
                                            value_list=plates).result_list
        if len(plates_records) < len(plates):
            existing_plate_ids_sapio = {plate_record.get_field_value("PlateId") for plate_record in plates_records}
            missing_plates = set(plates) - existing_plate_ids_sapio
            return {"status": "error", "message": f"Plates {', '.join(missing_plates)} not found in Sapio. Please create the plate(s) in Sapio first."}
    """
    return {"status": "success"}

def post_save_edp_profile(profile):
    project_record = None

    current_user = helpers.get_current_user()
    #share profile with customer in COPO
    customer_emails = profile.get("customer_emails","")
    if customer_emails:
        emails = [email.strip() for email in customer_emails.split(";")
                   if email.strip()]

        group_id = None
        groups = CopoGroup().get_group_by_profile(profile_id=profile["_id"])
        if not groups:
            #create group for profile
            group_id = CopoGroup().create_group_for_profile(profile_id=profile["_id"], group_name=profile["title"], owner_id=profile["user_id"])
        else:
            group_id = groups[0]["_id"]
        current_shared_users = CopoGroup().get_users_for_group_info(group_id=group_id)
 
        missing_user_emails = set(emails)
        incorrect_shared_user_ids = [str(user["id"]) for user in current_shared_users if user.get("email","") not in emails]
        users = User.objects.filter(email__in = emails).values('id', 'email','first_name')
        if users:
            missing_user_emails = set(emails) - {user["email"] for user in users}            
            new_shared_user = {str(user['id']) : user for user in users if 
                                    user['id'] not in current_shared_users 
                                    and user['email'] != current_user.email
                                }
            CopoGroup().add_users_to_group(group_id=group_id, user_ids=list(new_shared_user.keys()))
            #send email to new_shared_user_ids to notify them of shared profile
            Email().notify_shared_profile_to_existing_user(profile, new_shared_user.values())

        CopoGroup().remove_users_from_group(group_id=group_id, user_ids=incorrect_shared_user_ids)

        if missing_user_emails:
            #create customer_emails_tokens for these emails
            customer_emails_tokens = { str(uuid.uuid4()):email for email in missing_user_emails}
            Profile().get_collection_handle().update_one({"_id":profile["_id"]},{"$set":{"customer_emails_tokens": customer_emails_tokens}})
            #send email to these users with token link to join the profile
            Email().notify_shared_profile_to_not_exist_user(profile, customer_emails_tokens)
    try:
        #update /create Sapio Project
        if not profile.get("sapio_project_id",""):
            project_records = Sapio().dataRecordManager.add_data_records_with_data(data_type_name="Project", field_map_list=[{"ProjectName": profile.get("jira_ticket_number",""),
                                                                                                    "ProjectDesc": profile.get("description",""),
                                                                                                    "C_SampleCount": profile.get("no_of_samples",0),
                                                                                                    "C_BudgetHolder": profile.get("budget_user","")}])            
            
            sapio_project_id = project_records[0].get_field_value('C_ProjectIdentifier')
            profile["sapio_project_id"] = sapio_project_id
            Profile().get_collection_handle().update_one({"_id":profile["_id"]},{"$set":{"sapio_project_id":sapio_project_id}})
            project_record = project_records[0]
            #add project to Directory 1
            directories = Sapio().dataRecordManager.query_data_records(data_type_name="Directory", 
                                                        data_field_name="RecordId", 
                                                        value_list=[1]).result_list
            directory_record = directories[0]
            directory: PyRecordModel = Sapio().inst_man.add_existing_record(directory_record)  
            Sapio().relationship_man.load_children([directory], 'Project')
            project : PyRecordModel = Sapio().inst_man.add_existing_record(project_record)
            directory.add_child(project)

        else:
            project_records = Sapio().dataRecordManager.query_data_records(data_type_name="Project", 
                                                        data_field_name="C_ProjectIdentifier", 
                                                        value_list=[profile["sapio_project_id"]]).result_list
            if not project_records or len(project_records) ==0:
                raise Exception(f"Failed to Find Sapio Project {profile["sapio_project_id"]}")
            project_record = project_records[0]
            project_record.set_field_value("ProjectName", profile.get("jira_ticket_number",""))
            project_record.set_field_value("ProjectDesc", profile.get("description",""))
            project_record.set_field_value("C_BudgetHolder", profile.get("budget_user",""))
            project_record.set_field_value("C_SampleCount", profile.get("no_of_samples",0))
            Sapio().dataRecordManager.commit_data_records([project_record])

        #attach samples to Sapio Project
        #get all samples for Sapio Project
        project: PyRecordModel = Sapio().inst_man.add_existing_record(project_record)  
        Sapio().relationship_man.load_children([project], 'Sample')
        samples_under_project: List[PyRecordModel] = project.get_children_of_type('Sample')
        samples_under_project = sorted(samples_under_project, key=lambda x: x.get_field_value("PlateId"))
        Sapio().relationship_man.load_children([project], 'Plate')
        plates_under_project: List[PyRecordModel] = project.get_children_of_type('Plate')

        assigned_plates_map_for_samples_to_delete = {}
        samples_to_remove = []

        existing_plate_ids_under_project = set()
        for plate in plates_under_project:
            existing_plate_ids_under_project.add(plate.get_field_value("PlateId"))

        #create samples if not exists
        if not samples_under_project or len(samples_under_project) < int(profile["no_of_samples"]):    
            existing_no_of_samples = len(samples_under_project) if samples_under_project else 0
            sample_records = Sapio().dataRecordManager.add_data_records_with_data(data_type_name="Sample", 
                                                                                  field_map_list=[{"ExemplarSampleType": profile["sample_type"], 
                                                                                                  "ContainerType": profile["container_type"],
                                                                                                  "C_LibraryType": profile.get("library_type","")}
                                                                                                  for _ in range(existing_no_of_samples, int(profile["no_of_samples"]))])
            samples : List[PyRecordModel] = Sapio().inst_man.add_existing_records(sample_records)
            project.add_children(samples)
            samples_under_project.extend(samples)            

        #delete samples if more than required
        diff = len(samples_under_project) - int(profile["no_of_samples"])
        if diff > 0:
            for sample in samples_under_project:
                if not sample.get_field_value("C_CustomerSampleName"):
                    samples_to_remove.append(sample)
                    diff -=1
                    assigned_plate_id = sample.get_field_value("PlateId") #PlateId is not always used, check StorageUnitPath as well, TBD
                    if assigned_plate_id:
                        if assigned_plate_id not in assigned_plates_map_for_samples_to_delete:
                            assigned_plates_map_for_samples_to_delete[assigned_plate_id] = []
                        assigned_plates_map_for_samples_to_delete[assigned_plate_id].append(sample)
                if diff <=0:
                    break
            if diff >0:
                raise Exception(f"Sapio Project {profile['sapio_project_id']} has customer samples associated. Cannot decrease the no. of samples.")

            project.remove_children(samples_to_remove)
               
            samples_under_project = [s for s in samples_under_project if s not in samples_to_remove]
        

        #update existing sample
        for sample in samples_under_project:
            sample.set_field_value("ExemplarSampleType", profile["sample_type"])
            sample.set_field_value("ContainerType", profile["container_type"])
            sample.set_field_value("C_LibraryType", profile.get("library_type",""))    
        
        #attach plate to Sapio Project, assume it is  96 well plate (8 rows x 12 columns)
        #create plate if necessary        
        
        """
        plates_str = profile.get("sapio_plate_ids","").strip()
        if plates_str:
            plates = plates_str.split(",")
            missing_plates = set(plates) - existing_plate_ids_under_project
            unwanted_plates = existing_plate_ids_under_project - set(plates)

            missing_plate_records = Sapio().dataRecordManager.query_data_records(data_type_name="Plate", 
                                                data_field_name="PlateId", 
                                                value_list=list(missing_plates)).result_list
            existing_plate_ids_sapio = set()
            for missing_plate_record in missing_plate_records:
                existing_plate_ids_sapio.add(missing_plate_record.get_field_value("PlateId"))
            
            existing_plate_record_models: List[PyRecordModel] = Sapio().inst_man.add_existing_records(missing_plate_records)  

            project.add_children(existing_plate_record_models)

            missing_plates_not_in_sapio = missing_plates - existing_plate_ids_sapio

            if missing_plates_not_in_sapio:
                raise Exception(f"Plates {', '.join(missing_plates_not_in_sapio)} not found in Sapio. Please create these plates in Sapio first.")
                            
            if unwanted_plates:
                #check if not attached samples to these plates
                for sample in samples_under_project:
                    assigned_plate = sample.get_field_value("PlateId")
                    if assigned_plate in unwanted_plates:
                        raise Exception(f"Sapio Project {profile['sapio_project_id']} has samples associated with plate {assigned_plate}. Cannot remove this plate from profile.")

                plate_records = Sapio().dataRecordManager.query_data_records(data_type_name="Plate", 
                                                data_field_name="PlateId", 
                                                value_list=list(unwanted_plates)).result_list           
                #remove plate from Sapio project
                plate_record_models: List[PyRecordModel] = Sapio().inst_man.add_existing_records(plate_records)  
                project.remove_children(plate_record_models)
        """

        #Sapio().relationship_man.load_children([project], 'Plate')
        #plates_under_project: List[PyRecordModel] = project.get_children_of_type('Plate')
        plates_under_project_map = {plate.get_field_value("PlateId") : plate for plate in plates_under_project}
        
        #remove samples from plates
        for plate_id, samples in assigned_plates_map_for_samples_to_delete.items():
            plate = plates_under_project_map.get(plate_id,None)
            if plate:
                plate.remove_children(samples)

        samples_without_plate = set()
        if samples_under_project:
            for sample in samples_under_project:
                assigned_plate = sample.get_field_value("PlateId")
                if not assigned_plate:
                    assigned_plate = sample.get_field_value("StorageUnitPath")
                    if not assigned_plate:
                        samples_without_plate.add(sample)

        Sapio().relationship_man.load_children(plates_under_project, 'Sample')        
        if samples_without_plate:
            for plate in plates_under_project:
                sample_for_plate: List[PyRecordModel] = []
                plate_assignments = {( str(column), row): False for column in range(1,13) for row in ["A", "B","C","D","E","F","G","H"]} #12 columns, 8 rows
                samples_under_plate: List[PyRecordModel] = plate.get_children_of_type('Sample')
                for sample in samples_under_plate:
                    plate_assignments_key = (sample.get_field_value("ColPosition"), sample.get_field_value("RowPosition"))
                    plate_assignments[plate_assignments_key] = True

                for _ in range(len(samples_under_plate), 96): #assume 96 well plate, 8X12
                    if not samples_without_plate:
                        break
                    key = next((k for k,v in plate_assignments.items() if not v), None)
                    if not key:
                        l.error("No more positions available in plate when assigning samples!")
                        break
                    sample = samples_without_plate.pop()
                    #sample_model = Sapio().inst_man.add_existing_record(sample)  
                    sample_for_plate.append(sample)
                    sample.set_field_value("PlateId", plate.get_field_value("PlateId"))
                    plate_assignments[key] = True
                    sample.set_field_value("ColPosition", key[0]) 
                    sample.set_field_value("RowPosition", key[1]) 
                if sample_for_plate:
                    plate.add_children(sample_for_plate)

        #create new plates if still samples without plate
        if samples_without_plate:
            no_of_plates_needed = math.ceil(len(samples_without_plate) / 96)
            new_plate_records = Sapio().dataRecordManager.add_data_records_with_data(data_type_name="Plate", 
                                                                                  field_map_list=[{"PlateSampleType": profile["sample_type"], 
                                                                                                  "PlateColumns": 12,"PlateRows": 8}
                                                                                                  for _ in range(no_of_plates_needed)])
            #attach plates to project
            new_plates : List[PyRecordModel] = Sapio().inst_man.add_existing_records(new_plate_records)
            project.add_children(new_plates)
            Sapio().relationship_man.load_children(new_plates, 'Sample')

            #assign samples to plates
            for plate in new_plates:
                sample_for_plate: List[PyRecordModel] = []
                plate_assignments = {( str(column), row): False for column in range(1,13) for row in ["A", "B","C","D","E","F","G","H"]} #12 columns, 8 rows
                for _ in range(96): #assume 96 well plate, 8X12
                    if not samples_without_plate:
                        break
                    key = next((k for k,v in plate_assignments.items() if not v), None)
                    if not key:
                        l.error("No more positions available in plate when assigning samples!")
                        break                    
                    sample = samples_without_plate.pop()
                    #sample_model = Sapio().inst_man.add_existing_record(sample)  
                    sample_for_plate.append(sample)
                    sample.set_field_value("PlateId", plate.get_field_value("PlateId"))                       
                    plate_assignments[key] = True
                    sample.set_field_value("ColPosition", key[0])
                    sample.set_field_value("RowPosition", key[1])         
                if sample_for_plate:
                    plate.add_children(sample_for_plate)

        Sapio().rec_man.store_and_commit()
        Sapio().dataRecordManager.delete_data_record_list([sample.get_data_record() for sample in samples_to_remove], recursive_delete=True)

        if samples_without_plate:
            l.error("Not all samples have been assigned to plates!")
            return  {"status":"warning", "message":"Profile has been saved. However, it is failed to update to Sapio! "}

        #assign plate locations, TBD

    except Exception as e:        
        l.exception(e)
        l.error("Failed to create or update sapio project for profile id: " + str(profile["_id"]) + " Error: " + str(e))
        return  {"status":"warning", "message":"Profile has been saved. However, it is failed to update to Sapio! "}
        
    return {"status": "success"}


def post_delete_edp_profile(profile):
    if profile.get("sapio_project_id",""):
        try:
            record = Sapio().dataRecordManager.query_data_records(data_type_name="Project", 
                                                    data_field_name="C_ProjectIdentifier", 
                                                    value_list=[profile["sapio_project_id"]]).result_list[0]
            Sapio().dataRecordManager.delete_data_record(record=record, recursive_delete=True)
        except Exception as e:
            l.exception(e)
            l.error("Failed to delete sapio profile for profile id: " + str(profile["_id"]) + " Error: " + str(e))
            return  {"status":"warning", "message":"Profile has been deleted. However, it is failed to delete from Sapio! "}
    return {"status": "success"}

def submit_edp_to_sapio(profile_id, study_id):
    profile = Profile().get_record(profile_id)
    if not profile:
        return {"status": "error", "message": f"Profile {profile_id} not found."}
    # Submit EDP data to Sapio
    singlecell = Singlecell().get_collection_handle().find_one({"profile_id": profile_id, "study_id": study_id})
    if not singlecell:
        return {"status": "error", "message": f"Singlecell submission for profile {profile_id} and study {study_id} not found."}    

    project_records = Sapio().dataRecordManager.query_data_records(data_type_name="Project", 
                                        data_field_name="C_ProjectIdentifier", 
                                        value_list=[study_id]).result_list
    if not project_records or len(project_records) ==0:
        return {"status": "error", "message": f"Sapio Project {study_id} not found."}
    singlecell_components = singlecell.get("components",{})

    study = singlecell_components.get("study",[])[0]
    #samples = singlecell_components.get("sample",[])

    project_record = project_records[0]
    #project_record.set_field_value("C_HandS", study.get("health_safety",""))
    #Sapio().dataRecordManager.commit_data_records([project_record])

    project: PyRecordModel = Sapio().inst_man.add_existing_record(project_record)  
    Sapio().relationship_man.load_children([project], 'Sample')
    samples_under_project: List[PyRecordModel] = project.get_children_of_type('Sample')
    samples_under_project_map = {s.get_field_value("SampleId"): s for s in samples_under_project}

    schemas = SinglecellSchemas().get_schema(schema_name=singlecell["schema_name"], target_id=singlecell["checklist_id"])
    
    sapio_mapping_df = pd.DataFrame(columns=["term_name","sapio_name"])
    for component_name, component_schema in schemas.items():
        component_schema_df = pd.DataFrame.from_records(component_schema)
        sapio_component_mapping_df = component_schema_df[(~pd.isna(component_schema_df["sapio_name"]) 
                                                           & (component_schema_df["sapio_name"].str.contains(":")) 
                                                           & (component_schema_df["term_manifest_behavior"]!="protected"))
                                                           ][["term_name","sapio_name"]]
        sapio_mapping_df = pd.concat([sapio_mapping_df, sapio_component_mapping_df], ignore_index=True)
        #sapio_mapping.update({row["term_name"]: row.get("sapio_name","") for index, row in component_schema_df.iterrows() if row.get("sapio_field","")})

    sapio_mapping_df["sapio_object"] = sapio_mapping_df["sapio_name"].apply(lambda x: x.split(":")[0]  )
    sapio_mapping_df["sapio_field"] = sapio_mapping_df["sapio_name"].apply(lambda x: x.split(":")[1]  )
    sapio_mapping_df.drop(columns=["sapio_name"], inplace=True)
    sapio_object_dict = {}
    for c, sapio_object_df in sapio_mapping_df.groupby("sapio_object", sort=False):
        # component_schemas_df = schemas_df[schemas_df['component_name']== c]
        sapio_object_df.set_index("term_name", inplace=True)
        sapio_object_dict[c] = sapio_object_df["sapio_field"].to_dict()

    project_sapio_mapping = sapio_object_dict.get("Project",{})
    sample_sapio_mapping = sapio_object_dict.get("Sample",{})

    for component_name, component_schema in schemas.items():
        #if component_name == "study":
        #    continue
        
        component_schema_df = pd.DataFrame.from_records(component_schema)
        component_data_df = pd.DataFrame.from_records(singlecell_components.get(component_name,[]))
        if component_data_df.empty:
            continue

        columns =  component_data_df.columns 
        component_data_df.drop(columns=[column for column in columns if column not in component_schema_df["term_name"].values]   
                               , axis=1, inplace=True)

        if component_name == "study":
            for index, row in component_data_df.iterrows():
                for column in component_data_df.columns:
                    sapio_field = project_sapio_mapping.get(column, "")
                    if sapio_field:
                        project.set_field_value(sapio_field, row[column])
                    sapio_field = sample_sapio_mapping.get(column, "")
                    if sapio_field:
                        #set to all samples under project
                        for sapio_sample in samples_under_project:
                            sapio_sample.set_field_value(sapio_field, row[column])

        if component_name == "sample":
            for index, row in component_data_df.iterrows():
                sapio_sample_id = row.get("sample_id","")
                sapio_sample = samples_under_project_map.get(sapio_sample_id,None)
                if not sapio_sample:
                    l.error(f"Sample with Sample ID {sapio_sample_id} not found in Sapio Project {study_id}. Skipping...")
                    continue
                for column in component_data_df.columns:
                    sapio_field = sample_sapio_mapping.get(column, "")
                    if sapio_field:
                        sapio_sample.set_field_value(sapio_field, row[column])
                
    Sapio().rec_man.store_and_commit()

    return {"status": "success", "message": f"EDP data submitted to Sapio Project {study_id} successfully."}


def join_shared_edp_profile(profile, token):
    type = profile["type"]
    if type != "ei_edp":
        return {"status": "error", "message": f"Profile {profile['_id']} is not an EDP profile."}
    user = helpers.get_current_user()
    if user.id == profile["user_id"]:
        return {"status": "error", "message": f"Profile owner cannot join the profile."}

    if user.email is None:
        email = profile.get("customer_emails_tokens", {}).get(token, "")
        if not email:
            return {"status": "error", "message": f"User is not authorised to join the profile."}
        #update user's email
        user.email = email
        user.save()
        
    customer_emails = profile.get("customer_emails","")
    if customer_emails:
        emails = [email.strip() for email in customer_emails.split(";")
                   if email.strip()]

        if user.email in emails:
            groups = CopoGroup().get_group_by_profile(profile_id=profile["_id"])
            if not groups:
                #create group for profile
                group_id = CopoGroup().create_group_for_profile(profile_id=profile["_id"], group_name=profile["title"], owner_id=profile["user_id"])
            else:
                group_id = groups[0]["_id"]
            CopoGroup().add_user_to_group(group_id=group_id, user_id=str(user.id))
            return {"status": "success"}
        return {"status": "error", "message": f"User {user.email} is not authorised to join the profile."}