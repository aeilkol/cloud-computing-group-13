#!/bin/sh

# copy interceptor to microservices
cp runtime_interceptor/runtime_interceptor.py microservices/data_delivery/runtime_interceptor.py
cp runtime_interceptor/runtime_interceptor.py microservices/data_analysis/runtime_interceptor.py

# copy redirect output
sh scripts/copy_redirect_output.sh

# build protobufs
sh scripts/build_all_protobufs.sh protobufs

# build docker containers
docker build -f outbound/Dockerfile . -t outbound --no-cache --network=host
docker build -f microservices/data_delivery/Dockerfile . -t data_delivery --no-cache --network=host
docker build -f microservices/data_analysis/Dockerfile . -t data_analysis --no-cache --network=host
docker build -f microservices/administrator_analysis/Dockerfile . -t administrator_analysis --no-cache --network=host
docker build -f microservices/logging/Dockerfile . -t logging --no-cache --network=host