import boto3
import argparse
import subprocess
import yaml
import time
import pandas as pd
from botocore.exceptions import NoCredentialsError, PartialCredentialsError

"""
This script scans all Data Prepper pipeline configurations in the DataPrepperPipelineConfigurations table across
multiple AWS accounts and regions, and identifies risky pipelines that either:

- Have acknowledgments enabled (`acknowledgments: true`), or
- Have persistent buffer enabled (`minBufferUnits` or `maxBufferUnits` > 0),

but **do not define a Dead Letter Queue (DLQ)** under the `sink` configuration.

It saves a detailed CSV per region/stage with the list of risky pipelines, and generates a summary CSV with total counts
and risk ratios.

Before running, ensure CONTROL_PLANE_ACCOUNT_MAPPING is correctly populated with account information.

Usage:

python3 scan-risky-pipelines.py --output-path "/path/to/output/folder"
"""

CONTROL_PLANE_ACCOUNT_MAPPING = {
    "prod": {
        "us-east-1": "650455509444",
        "us-east-2": "642133779026",
        "us-west-1": "110293142570",
        "us-west-2": "647705926003",
        "eu-west-1": "321843683841",
        "eu-west-2": "494103247145",
        "eu-west-3": "958100456306",
        "eu-north-1": "766161543942",
        "eu-south-1": "483623536314",
        "eu-central-1": "793214851599",
        "ap-northeast-1": "488939961373",
        "ap-northeast-2": "445771684395",
        "ap-southeast-1": "250245820245",
        "ap-southeast-2": "024043984225",
        "ap-south-1": "008739254458",
        "sa-east-1": "706976067050",
        "ca-central-1": "059841243330"
    },
}

def auth(region, stage):
    account = CONTROL_PLANE_ACCOUNT_MAPPING[stage][region]
    command = [
        'ada', 'credentials', 'update',
        '--account', account,
        '--role', 'ReadOnly',
        '--provider', 'isengard',
        '--once'
    ]
    print(f"[INFO] Authenticating to {account} in {region} for stage {stage} using ReadOnly role...")
    subprocess.run(command, check=True)
    boto3.setup_default_session()
    time.sleep(1)

def extract_account_id_and_pipeline_name(item):
    return item.get('accountId'), item.get('pipelineName')

def parse_config_as_yaml(config_body_str):
    try:
        return yaml.safe_load(config_body_str)
    except yaml.YAMLError as e:
        print(f"[WARN] Could not parse pipeline config body as YAML: {e}")
        return None

def is_ack_true(config_json):
    try:
        source = config_json.get("source", {})
        if not isinstance(source, dict):
            return False

        for source_type, source_config in source.items():
            if isinstance(source_config, dict) and source_config.get("acknowledgments") is True:
#                 print(f"[INFO] {source_type} source with acknowledgments = true")
                return True

        return False
    except Exception as e:
        print(f"[WARN] Failed to check acknowledgments: {e}")
        return False

def has_dlq_defined(config_json):
    try:
        sink_list = config_json.get("sink", [])
        for sink_entry in sink_list:
            for _, sink_config in sink_entry.items():
                if not isinstance(sink_config, dict):
                    continue
                dlq = sink_config.get("dlq")
                if isinstance(dlq, dict) and "s3" in dlq and isinstance(dlq["s3"], dict):
                    return True
        return False
    except Exception as e:
        print(f"[WARN] Failed to check DLQ: {e}")
        return False

def scan_all_items(table):
    items = []
    response = table.scan()
    items.extend(response.get("Items", []))

    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        items.extend(response.get("Items", []))

    return items

def is_pipeline_risky(config_body_str):
    config = parse_config_as_yaml(config_body_str)
    if config is None:
        return False

    pipeline_keys = [k for k in config if k != "version"]
    if not pipeline_keys:
        return False
    root_key = pipeline_keys[0]

    pipeline_config = config.get(root_key)
    if not isinstance(pipeline_config, dict):
#         print(f"[WARN] Skipping pipeline '{root_key}' due to invalid structure: {type(pipeline_config)} - {pipeline_config}")
        return False

    has_ack = is_ack_true(pipeline_config)
    unit_alloc = pipeline_config.get("unitAllocation", {})
    has_buffer = unit_alloc.get("maxBufferUnits", 0) > 0 or unit_alloc.get("minBufferUnits", 0) > 0

    if not (has_ack or has_buffer):
        return False  # No need to check DLQ if pipeline is not risky based on ack/buffer

    has_dlq = has_dlq_defined(pipeline_config)
#     print(f"[DEBUG] Pipeline '{root_key}': ack={has_ack}, buffer={has_buffer}, dlq={has_dlq}")
    return not has_dlq

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan all pipelines for ack/buffer w/o DLQ and save results by region/stage")
    parser.add_argument("--output-path", required=True, help="Folder to save result CSVs")
    args = parser.parse_args()


    summary_data = []
    total_scanned_all = 0
    total_risky_all = 0

    for stage, region_map in CONTROL_PLANE_ACCOUNT_MAPPING.items():
        for region, account in region_map.items():
            print(f"\n==========================")
            print(f"[INFO] Processing stage={stage}, region={region}")
            print(f"==========================")

            try:
                if not account:
                    print(f"[SKIP] No account configured for {region} in {stage}, skipping...")
                    continue

                # Auth and access table
                auth(region, stage)
                dynamodb = boto3.resource("dynamodb", region_name=region)
                table = dynamodb.Table("DataPrepperPipelineConfigurations")

                print("region name: ", region, " stage ", stage)
                items = scan_all_items(table)
#                 items = response.get("Items", [])

                print(f"[INFO] Total pipelines scanned: {len(items)}")
                risky_pipelines = []

                for item in items:
                    config_body = item.get("pipelineConfigurationBody", "")
                    account_id, pipeline_name = extract_account_id_and_pipeline_name(item)

                    if is_pipeline_risky(config_body):
#                         print(f"[ALERT] {pipeline_name} (account {account_id}) is risky")
                        risky_pipelines.append({
                            "accountId": account_id,
                            "pipelineName": pipeline_name,
                            "pipelineArn": item.get("pipelineArn", "")
                        })
#                     else:
#                         print(f"[OK] {pipeline_name} is safe")

                if risky_pipelines:
                    filename = f"Pipelines_DLQ_result_{region.replace('-', '_')}_{stage}.csv"
                    output_file = f"{args.output_path.rstrip('/')}/{filename}"
                    df = pd.DataFrame(risky_pipelines)
                    df.to_csv(output_file, index=False)
                    print(f"[SAVED] {len(risky_pipelines)} risky pipelines saved to {output_file}")
                else:
                    print("[RESULT] No risky pipelines found for this region/stage.")

                total_scanned = len(items)
                total_risky = len(risky_pipelines)
                risk_ratio = round((total_risky / total_scanned) * 100, 2) if total_scanned else 0.0

                summary_data.append({
                    "stage": stage,
                    "region": region,
                    "total_pipelines": total_scanned,
                    "risky_pipelines": total_risky,
                    "risk_ratio_percent": risk_ratio
                })

                total_scanned_all += total_scanned
                total_risky_all += total_risky

            except Exception as e:
                print(f"[ERROR] Failed to process region={region}, stage={stage}: {e}")
    overall_risk_ratio = round((total_risky_all / total_scanned_all) * 100, 2) if total_scanned_all else 0.0
    summary_data.append({
        "stage": "ALL",
        "region": "ALL",
        "total_pipelines": total_scanned_all,
        "risky_pipelines": total_risky_all,
        "risk_ratio_percent": overall_risk_ratio
    })

    # --- Save summary CSV ---
    summary_df = pd.DataFrame(summary_data)
    summary_output_path = f"{args.output_path.rstrip('/')}/DLQ_Scan_Summary.csv"
    summary_df.to_csv(summary_output_path, index=False)
    print(f"\n[SUMMARY] Summary saved to {summary_output_path}")