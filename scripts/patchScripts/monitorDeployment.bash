#!/bin/bash

deployment_id=$1

# This script gets the statuses of a deployment and writes it to the relevant CSV file to keep track of 
# a large patching operation. Also prints to the terminal for tracking the progress

if [ -z "$deployment_id" ]; then
  echo "Usage: $0 <deployment_id>"
  exit 1
fi

if [ -z $REGION ]; then
  echo "export the REGION variable before running"
  exit 1
fi

function PrintPipelines() {
  pipelines=("$@")
  if [[ ! -z $pipelines ]]; then
    for pipeline in ${pipelines[@]}; do
      echo "            $pipeline"
    done
  fi
}

choice="null"
displayed=0
while [[ $choice != "e" ]]; do
  if [[ $displayed == 0 ]]; then
    displayed=1
    read -t 10 -p "    Enter to see deployment status. e to abort. Monitoring..." choice
  else
    read -t 10 -s choice
  fi
  if [[ $? == 1 ]] ; then
    choice=""
  else
    if [[ $choice == "e" ]]; then
      echo ""
      echo "    Aborting monitoring"
      exit 0
    else
      echo ""
      choice="m"
    fi
    displayed=0
  fi

  status_json=$(bash getStatuses.bash $deployment_id)

  skipped=$(echo $status_json | jq -r --arg region $REGION '.details.skipped[] | "arn:aws:osis:\($region):\(.accountId):pipeline/\(.pipelineName)"')
  provisioned=$(echo $status_json | jq -r --arg region $REGION '.details.provisioned[] | "arn:aws:osis:\($region):\(.accountId):pipeline/\(.pipelineName)"')
  in_progress=$(echo $status_json | jq -r --arg region $REGION '.details.inProgress[] | "arn:aws:osis:\($region):\(.accountId):pipeline/\(.pipelineName)"')
  failed=$(echo $status_json | jq -r --arg region $REGION '.details.failed[] | "arn:aws:osis:\($region):\(.accountId):pipeline/\(.pipelineName)"')
  complete=$(echo $status_json | jq -r --arg region $REGION '.details.complete[] | "arn:aws:osis:\($region):\(.accountId):pipeline/\(.pipelineName)"')
  aborted=$(echo $status_json | jq -r --arg region $REGION '.details.aborted[] | "arn:aws:osis:\($region):\(.accountId):pipeline/\(.pipelineName)"')

  # If everything is complete, show final status and exit. 
  if [[ -z $provisioned && -z $in_progress ]]; then
    echo ""
    echo "    ================================================="
    echo "    Deployment $deployment_id complete. Final status:"
    echo "        Skipped:"
    PrintPipelines $skipped
    echo "        Provisioned:"
    PrintPipelines $provisioned
    echo "        In Progress:"
    PrintPipelines $in_progress
    echo "        Failed:"
    PrintPipelines $failed
    echo "        Complete:"
    PrintPipelines $complete
    echo "        Aborted:"
    PrintPipelines $aborted
    echo "    ================================================="
    choice="e"
  fi

  # Show if deployment status desired
  if [[ $choice == "m" ]]; then
    echo "    ================================================="
    echo "    Deployment $deployment_id status:"
    echo "        Skipped:"
    PrintPipelines $skipped
    echo "        Provisioned:"
    PrintPipelines $provisioned
    echo "        In Progress:"
    PrintPipelines $in_progress
    echo "        Failed:"
    PrintPipelines $failed
    echo "        Complete:"
    PrintPipelines $complete
    echo "        Aborted:"
    PrintPipelines $aborted
    echo "    ================================================="
  fi

  # Update the CSV with the current status
  for pipeline_arn in $skipped; do
    bash updateDeploymentStatusCsv.bash $pipeline_arn s s
  done 
  for pipeline_arn in $provisioned; do
    bash updateDeploymentStatusCsv.bash $pipeline_arn s p
  done 
  for pipeline_arn in $in_progress; do
    bash updateDeploymentStatusCsv.bash $pipeline_arn s iP
  done
  for pipeline_arn in $failed; do
    bash updateDeploymentStatusCsv.bash $pipeline_arn s f
  done
  for pipeline_arn in $complete; do
    bash updateDeploymentStatusCsv.bash $pipeline_arn s c
  done
  for pipeline_arn in $aborted; do
    bash updateDeploymentStatusCsv.bash $pipeline_arn s a
  done

done


