#!/bin/bash
# Generate gRPC Python code from proto file

set -e

# Base directory
BASE_DIR="/Users/shubhajeetpradhan/Desktop/idea/spyMonk-query-platform/spyMonk-DB"
PROTO_DIR="$BASE_DIR/spymonk_enterprise/network/grpc/proto"
GEN_DIR="$BASE_DIR/spymonk_enterprise/network/grpc/gen"

# Create generation directory
mkdir -p "$GEN_DIR"
touch "$GEN_DIR/__init__.py"

echo "Generating gRPC code for spymonk.proto..."

python3 -m grpc_tools.protoc \
    -I"$BASE_DIR" \
    --python_out="$BASE_DIR" \
    --grpc_python_out="$BASE_DIR" \
    "$PROTO_DIR/spymonk.proto"

# The generated files will be in $PROTO_DIR since we used $BASE_DIR as output
# Move them to $GEN_DIR
mv "$PROTO_DIR/spymonk_pb2.py" "$GEN_DIR/"
mv "$PROTO_DIR/spymonk_pb2_grpc.py" "$GEN_DIR/"

# Fix imports in generated file
sed -i '' 's/import spymonk_pb2 as spymonk__pb2/from . import spymonk_pb2 as spymonk__pb2/' "$GEN_DIR/spymonk_pb2_grpc.py"

echo "Done! Generated files in $GEN_DIR"
