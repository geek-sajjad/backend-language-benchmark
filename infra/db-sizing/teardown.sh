#!/usr/bin/env bash
# Remove everything tagged Project=db-sizing in us-east-1. Safe to run more than once.
source "$(dirname "$0")/common.sh"

teardown_instances

sg="$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text)"
if [[ "$sg" != None && -n "$sg" ]]; then
  log "deleting security group $sg"
  for i in $(seq 1 30); do # ENIs of just-terminated instances can hold it for a little while
    aws ec2 delete-security-group --group-id "$sg" 2>/dev/null && break
    [[ $i == 30 ]] && die "could not delete security group $sg"
    sleep 10
  done
fi
if aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
  log "deleting key pair $KEY_NAME"
  aws ec2 delete-key-pair --key-name "$KEY_NAME"
fi
if aws ec2 describe-placement-groups --group-names "$PG_NAME" >/dev/null 2>&1; then
  log "deleting placement group $PG_NAME"
  aws ec2 delete-placement-group --group-name "$PG_NAME"
fi

log "what is left (must be empty):"
echo "  instances:        $(tagged_instances)"
echo "  security groups:  $(aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" --query 'SecurityGroups[].GroupId' --output text)"
echo "  key pairs:        $(aws ec2 describe-key-pairs --filters "Name=key-name,Values=$KEY_NAME" --query 'KeyPairs[].KeyName' --output text)"
echo "  placement groups: $(aws ec2 describe-placement-groups --filters "Name=group-name,Values=$PG_NAME" --query 'PlacementGroups[].GroupName' --output text)"
echo "  volumes:          $(aws ec2 describe-volumes --filters "Name=tag:Project,Values=$TAG" --query 'Volumes[].VolumeId' --output text)"
echo "  ENIs:             $(aws ec2 describe-network-interfaces --filters "Name=tag:Project,Values=$TAG" --query 'NetworkInterfaces[].NetworkInterfaceId' --output text)"
