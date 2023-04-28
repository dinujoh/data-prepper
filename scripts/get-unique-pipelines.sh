#!/opt/homebrew/bin/bash

# check if the AWS CLI is installed
if ! command -v aws &> /dev/null
then
    echo "AWS CLI not found. Please install the AWS CLI and try again."
    exit 1
fi

# check if jq is installed
if ! command -v jq &> /dev/null
then
    echo "jq not found. Please install jq and try again."
    exit 1
fi

# Declare the associate arrays
declare -A region_to_cp_account_id=(
    ['ap-northeast-1']="488939961373"
    ['ap-southeast-1']="250245820245"
    ['ap-southeast-2']="024043984225"
    ['eu-central-1']="793214851599"
    ['eu-west-1']="321843683841"
    ['eu-west-2']="494103247145"
    ['us-east-1']="650455509444"
    ['us-east-2']="642133779026"
    ['us-west-1']="110293142570"
    ['us-west-2']="647705926003"
)
declare -A region_to_canary_account_ids=(
    ['ap-northeast-1']="[\"773602833938\",\"505992770131\",\"382012671016\",\"512384385051\"]"
    ['ap-southeast-1']="[\"278707483210\",\"251659929690\",\"337737356911\",\"455093542881\",\"993895689380\",\"841692598829\",\"230635610364\",\"534869792916\"]"
    ['ap-southeast-2']="[\"716628331031\",\"041589693843\",\"993895689380\",\"014187015500\",\"455093542881\"]"
    ['eu-central-1']="[\"247360014714\",\"530706466004\",\"253800316840\",\"266539172451\"]"
    ['eu-west-1']="[\"558212920542\",\"293815226378\",\"994200650878\",\"397731839248\",\"870201406020\",\"188839567353\"]"
    ['eu-west-2']="[\"709440730813\",\"564158846712\",\"533490903600\",\"472920166441\"]"
    ['us-east-1']="[\"723977669261\",\"950162433161\",\"206345544268\",\"268126931667\",\"346825893329\",\"659617288443\",\"709387180454\",\"790849549214\",\"870201406020\"]"
    ['us-east-2']="[\"506804586020\",\"217320828639\",\"604824811722\",\"197153428489\",\"640752097817\",\"790849549214\"]"
    ['us-west-1']="[\"584896679456\",\"163851864585\",\"751368480082\",\"023746198767\",\"23746198767\",\"767558553171\"]"
    ['us-west-2']="[\"190743040936\",\"840603571106\",\"425443753380\",\"455093542881\",\"841692598829\",\"388303208821\",\"176893235612\",\"193777858833\",\"314964498872\",\"578844260082\",\"66865889324\"]"
)

output_file="output.csv"
echo "region,accountId,pipelineName,timestamp,status" > "$output_file"

for region in "${!region_to_cp_account_id[@]}"; do
    canary_account_ids="${region_to_canary_account_ids[$region]}"
    cp_account_id="${region_to_cp_account_id[$region]}"

    ada credentials update --account $cp_account_id --role ReadOnly --provider isengard --sim V800442086 --once

    query_result=$(aws dynamodb scan \
        --region "$region" \
        --table-name DataPrepperPipelineConfigurations \
        --filter-expression "NOT contains(:canary_account_ids, accountId)" \
        --expression-attribute-values '{":canary_account_ids": {"SS": '$canary_account_ids'}}' \
        --projection-expression "accountId, pipelineName, lifecycleStatus, createdAt" \
        --no-paginate \
        --output json)

    items=$(echo "$query_result" | jq -c '.Items[]')

    while IFS= read -r item; do
        accountId=$(echo "$item" | jq -r '.accountId.S')
        pipelineName=$(echo "$item" | jq -r '.pipelineName.S')
        status=$(echo "$item" | jq -r '.lifecycleStatus.S')
        epochMillis=$(echo "$item" | jq -r '.createdAt.N')
        epochSeconds=$((epochMillis/1000))
        timestamp=$(printf '%(%FT%T%z)T\n' $epochSeconds)

        echo "$region,$accountId,$pipelineName,$timestamp,$status" >> "$output_file"
    done <<< "$items"
done
