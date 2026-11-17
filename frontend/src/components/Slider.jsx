import React, { useState } from 'react';

export default function Slider({ id, label, min, max, step = 1, value, onChange, unit = '', disabled = false, help }) {
  const [draft, setDraft] = useState(null);
  const valid = (raw) => raw !== '' && Number.isFinite(Number(raw)) && Number(raw) >= min && Number(raw) <= max && Math.abs((Number(raw) - min) / step - Math.round((Number(raw) - min) / step)) < 1e-8;
  const invalid = draft !== null && !valid(draft);
  return (
    <div className="slider-control">
      <div className="slider-heading"><label htmlFor={`${id}-range`}>{label}</label><output htmlFor={`${id}-range`}>{value.toLocaleString()} {unit}</output></div>
      <div className="slider-inputs">
        <input id={`${id}-range`} type="range" min={min} max={max} step={step} value={value}
          disabled={disabled} aria-describedby={`${id}-help`} aria-valuetext={`${value} ${unit}`}
          onChange={(e) => { setDraft(null); onChange(Number(e.target.value)); }} />
        <input id={id} type="number" aria-label={label} min={min} max={max} step={step}
          disabled={disabled} value={draft ?? value} aria-invalid={invalid} aria-describedby={`${id}-help`}
          onChange={(e) => { const raw = e.target.value; setDraft(raw); if (valid(raw)) onChange(Number(raw)); }}
          onBlur={() => setDraft(null)} />
      </div>
      <div className="slider-bounds"><span>{min.toLocaleString()} {unit}</span><span>{max.toLocaleString()} {unit}</span></div>
      <small id={`${id}-help`} className={invalid ? 'slider-error' : 'slider-help'}>
        {invalid ? `Enter ${min}–${max} in steps of ${step}. Invalid edits are discarded on blur.` : help}
      </small>
    </div>
  );
}
