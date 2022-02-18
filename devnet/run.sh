#!/bin/bash
./goal network start -r devnet
./goal node status -d devnet/primary -w 1000
