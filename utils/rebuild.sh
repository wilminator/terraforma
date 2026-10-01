#!/bin/env bash
docker compose build test && docker compose build postgres-test && docker compose build mysql-test