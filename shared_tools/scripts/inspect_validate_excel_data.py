import pymongo
import urllib.parse
import pandas as pd
import os
from bson.objectid import ObjectId
from io import BytesIO

def get_env(env_key):
    env_value = str()
    if env_key in os.environ:
        env_value = os.getenv(env_key)

    # resolve for file assignment
    file_env = os.environ.get(env_key + '_FILE', str())
    if len(file_env) > 0:
        try:
            with open(file_env, 'r') as mysecret:
                data = mysecret.read().replace('\n', str())
                env_value = data
        except:
            pass
    return env_value

# Configure MongoDB database then, connect to it
username = urllib.parse.quote_plus(get_env('MONGO_USER'))
password = urllib.parse.quote_plus(get_env('MONGO_USER_PASSWORD'))
host = urllib.parse.quote_plus(get_env('MONGO_HOST'))
port = urllib.parse.quote_plus(get_env('MONGO_PORT'))
mongoClient = pymongo.MongoClient(
    f'mongodb://{username}:{password}@{host}:{port}/')
mongoDB = mongoClient['copo_mongo']


collection = mongoDB["ValidationQueueCollection"]
cursor = collection.find({"_id": ObjectId("6930037743722b9bd5e76ab8")}) # Replace with your desired ObjectId
for qm in cursor:
    bytestring = BytesIO(qm["manifest_data"])
    data = pd.read_pickle(bytestring)
    print(data["TAXON_ID"])

