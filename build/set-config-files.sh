#! /bin/bash

# Check if blockchain argument is provided
if [ $# -eq 0 ]; then
    echo "Error: No blockchain argument provided"
    echo "Usage: $0 <blockchain> [task_error_report_automatic] [tp_fallback]"
    exit 1
fi

# Get the blockchain argument
blockchain=$1
task_error_report_automatic=${2:-false}
tp_fallback=${3:-device_map}

if [[ "$task_error_report_automatic" != "true" && "$task_error_report_automatic" != "false" ]]; then
    echo "Error: task_error_report_automatic must be true or false"
    exit 1
fi

if [[ "$tp_fallback" != "device_map" && "$tp_fallback" != "reduce_gpus" ]]; then
    echo "Error: tp_fallback must be device_map or reduce_gpus"
    exit 1
fi

# Define an array of file pairs with names
# Format: "name|source_path|destination_path"
declare -a file_pairs=(
    "WebUI|./src/webui/src/config.${blockchain}.json|./src/webui/src/config.json"
    "Node Docker|./build/docker/config.yml.${blockchain}|./build/docker/config.yml.example"
    "Node MacOS|./build/macos/config.yml.${blockchain}|./build/macos/config.yml.example"
    "Node Windows|./build/windows/config.yml.${blockchain}|./build/windows/config.yml.example"
)

# Process each file pair
for pair in "${file_pairs[@]}"; do
    # Split the pair into name, source, and destination
    IFS="|" read -r name source_file destination_file <<< "$pair"

    echo "Processing configuration for: $name"

    # Check if source file exists
    if [ ! -f "$source_file" ]; then
        echo "Error: Source file $source_file does not exist"
        continue
    fi

    # Copy the file
    echo "Copying $source_file to $destination_file"
    cp "$source_file" "$destination_file"

    if [ $? -eq 0 ]; then
        echo "Configuration file for $name successfully updated for $blockchain"
    else
        echo "Error: Failed to copy configuration file for $name"
    fi

    echo ""
done

docker_config="./build/docker/config.yml.example"
sed -i -E "/^task_error_report:$/ { n; s/^([[:space:]]*automatic:).*/\1 ${task_error_report_automatic}/; }" "$docker_config"
if ! grep -q "^  automatic: ${task_error_report_automatic}$" "$docker_config"; then
    echo "Error: Failed to configure task_error_report.automatic"
    exit 1
fi

sed -i -E "s/^([[:space:]]*tp_fallback:).*/\1 ${tp_fallback}/" "$docker_config"
if ! grep -q "^  tp_fallback: ${tp_fallback}$" "$docker_config"; then
    echo "Error: Failed to configure task_config.tp_fallback"
    exit 1
fi

echo "Configuration update completed."
