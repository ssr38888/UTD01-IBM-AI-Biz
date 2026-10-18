export function createClaimPanel(sessionFetch) {
  const panel = document.getElementById('claimPanel');
  const status = document.getElementById('claimStatus');
  let revision = 0;
  const probability = value => value === null ? '—' : `${(value * 100).toFixed(2)}%`;
  return async event => {
    const ticket = ++revision;
    document.getElementById('baselineScore').textContent = '—';
    document.getElementById('adjustedScore').textContent = '—';
    document.getElementById('relativeEffect').textContent = '—';
    document.getElementById('beforeIndex').textContent = '—';
    document.getElementById('afterIndex').textContent = '—';
    document.getElementById('beforeRing').setAttribute('stroke-dashoffset', '100');
    document.getElementById('afterRing').setAttribute('stroke-dashoffset', '100');
    document.getElementById('rangeRing')?.setAttribute('visibility','hidden');
    document.getElementById('afterRangeBand')?.setAttribute('visibility','hidden');
    for (const id of ['beforeBar', 'afterBar']) {
      document.getElementById(id).setAttribute('y', '150');
      document.getElementById(id).setAttribute('height', '0');
    }
    document.getElementById('effectMeter').value = 0;
    document.getElementById('riskIndex').textContent = '';
    document.getElementById('adjustmentStatus').textContent = 'Loading estimate…';
    document.getElementById('claimEvidenceStatus').textContent = '';
    document.getElementById('fallIntensity').textContent = '—';
    document.getElementById('intensityMetrics').textContent = '';
    panel.hidden = !event;
    if (!event) return;
    document.getElementById('claimIncident').textContent = `Event #${event.seq} · ${new Date(event.detected_at_ms).toLocaleString()} · ${event.images.length} captured photos`;
    status.textContent = 'Loading automatic scores…';
    try {
      const response = await sessionFetch(`/api/events/${encodeURIComponent(event.id)}/dispute-score`);
      const data = await response.json();
      if (ticket !== revision) return;
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Scores could not be loaded. Refresh to try again.');
      document.getElementById('baselineScore').textContent = probability(data.baseline_probability);
      const hasRange = data.adjusted_probability_range !== null && !!data.adjusted_probability_range;
      const adjusted = data.adjusted_probability !== null || hasRange;
      const photoOnly = data.score_basis === 'verified_photos_policy';
      const reduction = hasRange ? Math.round(data.assumed_relative_reduction_range.max * 100) : adjusted ? Math.round(data.assumed_relative_reduction * 100) : 0;
      const minReduction = hasRange ? Math.round(data.assumed_relative_reduction_range.min * 100) : reduction;
      const reductionText = hasRange ? `${minReduction}–${reduction}` : `${reduction}`;
      const afterText = hasRange ? `${100-reduction}–${100-minReduction}` : `${100-reduction}`;
      panel.dataset.range=String(hasRange);
      document.getElementById('relativeEffect').textContent = `${reductionText}%`;
      document.getElementById('beforeIndex').textContent = '100%';
      document.getElementById('afterIndex').textContent = `${afterText}%`;
      document.getElementById('wheelDesc').textContent = `Remaining relative risk: ${afterText}%. Assumed reduction: ${reductionText}%. ${hasRange?'Amber marks the scenario range, not a confidence interval.':''} Normalized to baseline, not probabilities.`;
      document.getElementById('barDesc').textContent = `Before evidence: index 100. After evidence: index ${afterText}. ${hasRange?'Shaded cap shows the range.':''} Actual dispute rates remain listed below.`;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (ticket !== revision) return;
        document.getElementById('beforeRing').setAttribute('stroke-dashoffset', '0');
        document.getElementById('afterRing').setAttribute('stroke-dashoffset', String(reduction));
        document.getElementById('beforeBar').setAttribute('y', '40');
        document.getElementById('beforeBar').setAttribute('height', '110');
        const afterHeight = (100 - reduction) * 1.1;
        document.getElementById('afterBar').setAttribute('y', String(150 - afterHeight));
        document.getElementById('afterBar').setAttribute('height', String(afterHeight));
        if(hasRange && document.getElementById('rangeRing') && document.getElementById('afterRangeBand')) {
          const ring=document.getElementById('rangeRing');
          ring.setAttribute('stroke-dasharray',`${reduction-minReduction} ${100-(reduction-minReduction)}`);
          ring.setAttribute('stroke-dashoffset',String(-(100-reduction)));
          ring.setAttribute('visibility','visible');
          const band=document.getElementById('afterRangeBand');
          band.setAttribute('y',String(150-(100-minReduction)*1.1));
          band.setAttribute('height',String((reduction-minReduction)*1.1));
          band.setAttribute('visibility','visible');
        }
      }));
      document.getElementById('effectMeter').value = reduction;
      document.getElementById('riskIndex').textContent = `Relative risk index: 100 → ${afterText} (not a probability)`;
      document.getElementById('effectBasis').textContent = adjusted
        ? photoOnly ? 'Assumed scenario range, not a confidence interval or measured product effectiveness' : 'Scenario assumption, not measured product effectiveness'
        : 'No evidence adjustment applied';
      document.getElementById('adjustedScore').textContent = hasRange
        ? `${(data.adjusted_probability_range.min*100).toFixed(2)}–${(data.adjusted_probability_range.max*100).toFixed(2)}%`
        : probability(adjusted ? data.adjusted_probability : data.baseline_probability);
      document.getElementById('adjustmentStatus').textContent = adjusted
        ? photoOnly ? 'Assumption-based range · photo evidence only' : 'Assumption-based range'
        : 'No adjustment applied — evidence not verified';
      const intensity = data.intensity;
      document.getElementById('intensityHeading').textContent = photoOnly ? 'CAPTURED INCIDENT EVIDENCE' : 'OBSERVED FALL INTENSITY';
      document.getElementById('intensityMeaning').textContent = photoOnly ? 'Photo-only assessment; motion intensity not measured' : 'Motion intensity, not injury severity';
      document.getElementById('fallIntensity').textContent = photoOnly ? 'Photo evidence available' : adjusted ? intensity.label : 'Evidence could not be verified';
      document.getElementById('fallIntensity').dataset.level = photoOnly ? 'photo_evidence' : intensity.level;
      document.getElementById('intensityMetrics').textContent = intensity.level === 'insufficient_visibility'
        ? photoOnly ? `${data.evidence_photos} saved photos · evidence integrity verified` : 'Check the saved evidence before applying an adjustment.'
        : `${intensity.speed_body_heights_per_second.toFixed(2)} body heights/sec · ${(intensity.descent_ms / 1000).toFixed(2)} sec descent · ${intensity.visible_pose_samples}/${intensity.total_pose_samples} visible poses${intensity.observation_quality === 'limited_visibility' ? ' · limited visibility' : ''}`;
      document.getElementById('claimEvidenceStatus').textContent = adjusted
        ? `${data.evidence_photos} captured photos · evidence integrity verified · ${reductionText}% assumed relative reduction${hasRange?' range':''}`
        : 'No evidence adjustment: reliable visible descent and intact key photos are required.';
      status.textContent = `${data.population.recorded_disputes.toLocaleString()} recorded disputes / ${data.population.training_claims.toLocaleString()} historical claims (${data.population.period}). Population baseline, not an employee-specific prediction.`;
    } catch (error) { if (ticket === revision) status.textContent = error.message; }
  };
}
