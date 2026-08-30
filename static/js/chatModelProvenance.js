/** Select and update the response holder for a route-provenance event. */
export function applyModelRouteEventState(event, holder, roundHolder, defaultModel = '') {
  const target = event && event.round && roundHolder ? roundHolder : holder;
  if (!target) return null;

  target._requestedModel = (
    event.requested_model
    || event.selected_model
    || target._requestedModel
    || defaultModel
  );
  target._actualModel = (
    event.model
    || event.answered_by
    || target._actualModel
    || target._requestedModel
  );
  const hasEndpointRoute = Boolean(
    event.requested_endpoint_id
    || event.selected_endpoint_id
    || event.endpoint_id
    || event.answered_by_endpoint_id
    || event.requested_endpoint_label
    || event.selected_endpoint_label
    || event.endpoint_label
    || event.answered_by_endpoint_label
    || target._requestedEndpointLabel
  );
  if (hasEndpointRoute) {
    target._requestedEndpointId = (
      event.requested_endpoint_id
      || event.selected_endpoint_id
      || target._requestedEndpointId
      || null
    );
    target._requestedEndpointLabel = (
      event.requested_endpoint_label
      || event.selected_endpoint_label
      || target._requestedEndpointLabel
      || 'Selected route'
    );
    target._actualEndpointId = (
      event.endpoint_id
      || event.answered_by_endpoint_id
      || target._actualEndpointId
      || target._requestedEndpointId
      || null
    );
    target._actualEndpointLabel = (
      event.endpoint_label
      || event.answered_by_endpoint_label
      || target._actualEndpointLabel
      || target._requestedEndpointLabel
    );
  }
  return target;
}

/** Copy the active route into the bubble created for the next Agent round. */
export function inheritModelRouteState(holder, roundHolder, target, defaultModel = '') {
  if (!target) return null;
  const source = roundHolder || holder;
  target._requestedModel = source?._requestedModel || defaultModel;
  target._actualModel = source?._actualModel || target._requestedModel;
  if (source?._requestedEndpointLabel || source?._actualEndpointLabel) {
    target._requestedEndpointId = source?._requestedEndpointId || null;
    target._requestedEndpointLabel = source?._requestedEndpointLabel || 'Selected route';
    target._actualEndpointId = source?._actualEndpointId || target._requestedEndpointId;
    target._actualEndpointLabel = source?._actualEndpointLabel || target._requestedEndpointLabel;
  }
  return target;
}

/** Apply final/metrics provenance to the active round, not the first bubble. */
export function applyModelMetricsState(metrics, holder, roundHolder, defaultModel = '') {
  const target = roundHolder || holder;
  if (!target || !metrics) return target || null;
  const roundModels = Array.isArray(metrics.round_models) ? metrics.round_models : [];
  const roundModel = roundHolder && roundModels.length
    ? roundModels[roundModels.length - 1]
    : null;
  target._requestedModel = metrics.requested_model || target._requestedModel || defaultModel;
  target._actualModel = roundModel || metrics.model || target._actualModel || target._requestedModel;
  const roundEndpointIds = Array.isArray(metrics.round_endpoint_ids) ? metrics.round_endpoint_ids : [];
  const roundEndpointLabels = Array.isArray(metrics.round_endpoint_labels) ? metrics.round_endpoint_labels : [];
  if (
    metrics.requested_endpoint_label
    || metrics.endpoint_label
    || roundEndpointLabels.length
    || target._requestedEndpointLabel
  ) {
    target._requestedEndpointId = metrics.requested_endpoint_id || target._requestedEndpointId || null;
    target._requestedEndpointLabel = metrics.requested_endpoint_label || target._requestedEndpointLabel || 'Selected route';
    const hasRoundEndpointId = Boolean(roundHolder && roundEndpointIds.length);
    const hasRoundEndpointLabel = Boolean(roundHolder && roundEndpointLabels.length);
    target._actualEndpointId = hasRoundEndpointId
      ? roundEndpointIds[roundEndpointIds.length - 1]
      : (metrics.endpoint_id || target._actualEndpointId || target._requestedEndpointId);
    target._actualEndpointLabel = hasRoundEndpointLabel
      ? roundEndpointLabels[roundEndpointLabels.length - 1]
      : (metrics.endpoint_label || target._actualEndpointLabel || target._requestedEndpointLabel);
  }
  return target;
}
