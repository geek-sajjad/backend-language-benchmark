#!/usr/bin/env bash
# Read-only checks before launching anything (docs/tmp-db-sizing-plan.md "Pre-flight checks").
source "$(dirname "$0")/common.sh"

aws sts get-caller-identity --query Arn --output text
log "instance types offered in $AZ"
offered="$(aws ec2 describe-instance-type-offerings --location-type availability-zone \
  --filters "Name=location,Values=$AZ" "Name=instance-type,Values=m7a.2xlarge,c7a.xlarge" \
  --query 'InstanceTypeOfferings[].InstanceType' --output text)"
echo "  $offered"
ok=1
for t in m7a.2xlarge c7a.xlarge; do grep -qw "$t" <<<"$offered" || { echo "  MISSING: $t"; ok=0; }; done

log "current spot prices in $AZ (stop if above 2x the plan: m7a.2xlarge 0.41, c7a.xlarge 0.17)"
for t in m7a.2xlarge:0.41 c7a.xlarge:0.1702; do
  p="$(aws ec2 describe-spot-price-history --instance-types "${t%%:*}" --availability-zone "$AZ" \
    --product-descriptions Linux/UNIX --start-time "$(date -u +%FT%TZ)" --query 'SpotPriceHistory[0].SpotPrice' --output text)"
  over="$(awk -v p="$p" -v m="${t##*:}" 'BEGIN{print (p=="None"||p>m)?1:0}')"
  echo "  ${t%%:*}: \$$p/h $([[ $over == 1 ]] && echo '<-- TOO HIGH or unavailable')"
  [[ $over == 1 ]] && ok=0
done

log "vCPU quotas (need 8)"
for q in L-34B43A08 L-1216C47A; do
  echo "  $q: $(aws service-quotas get-service-quota --service-code ec2 --quota-code "$q" --query 'Quota.Value' --output text)"
done

log "left-overs tagged Project=$TAG"
left="$(tagged_instances)"
echo "  instances: ${left:-none}"
[[ -n "$left" ]] && ok=0
echo "  security group: $(aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" --query 'SecurityGroups[].GroupId' --output text)"
echo "  key pair: $(aws ec2 describe-key-pairs --filters "Name=key-name,Values=$KEY_NAME" --query 'KeyPairs[].KeyName' --output text)"
echo "  placement group: $(aws ec2 describe-placement-groups --filters "Name=group-name,Values=$PG_NAME" --query 'PlacementGroups[].GroupName' --output text)"
echo "  default subnet $SUBNET: $(aws ec2 describe-subnets --subnet-ids "$SUBNET" --query 'Subnets[0].[AvailabilityZone,VpcId]' --output text)"

[[ $ok == 1 ]] && log "pre-flight OK" || die "pre-flight failed, see above"
