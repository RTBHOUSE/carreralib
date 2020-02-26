import csv

from flask import Flask
from flask import render_template

from google.cloud import datastore
from google.oauth2 import service_account


credentials = service_account.Credentials \
    .from_service_account_file('./bigdatatech-warsaw-challenge-219525419ec7.json')
client = datastore.Client(project=credentials.project_id, credentials=credentials)
app = Flask(__name__)

@app.route("/")
def data_store():
    query = client.query(kind="race_results")
    query.order = ['best_lap']

    results = query.fetch(limit=10)
    return render_template('rank.html', results=results)


@app.route("/csv")
def csv_store():
    results = list()
    with open('results.csv', newline='') as csvfile:
        reader = csv.reader(csvfile, delimiter=',')

        for row in reader:
            results.append({
                'username' : row[0],
                'best_lap' : row[1]
            })

    results.sort(key=lambda result: int(result['best_lap']),reverse=False)
    return render_template('rank.html', results=results[:10])

if __name__ == "__main__":
    app.run()


