import React, { useEffect, useState } from 'react';

export function validInteger(value, min, max) {
  return value !== '' && Number.isInteger(Number(value)) && Number(value) >= min && Number(value) <= max;
}

export default function NumberSlider({ id, label, info, value, onChange, min = 1,
  max, inputMax = max, fallback = min, disabled = false, note }) {
  const valid = validInteger(value, min, inputMax);
  // Preserve larger saved/custom policies supported by the existing API.
  // Keep an extended scale stable while dragging back down from a custom value.
  const [extendedMax, setExtendedMax] = useState(max);
  const rangeMax = Math.max(max, extendedMax, valid ? Number(value) : max);
  useEffect(() => {
    if (valid && Number(value) > extendedMax) setExtendedMax(Number(value));
  }, [value, valid, extendedMax]);
  const normalize = () => {
    if (valid) return;
    const number = value === '' ? fallback : Number(value);
    onChange(Number.isFinite(number) ? Math.min(inputMax, Math.max(min, Math.round(number))) : fallback);
  };

  return (
    <div className="number-slider">
      <label htmlFor={id}>
        <span>{label} {info}</span>
        <input id={id} aria-label={label} type="number" min={min} max={inputMax} step="1"
          value={value ?? ''} disabled={disabled} aria-invalid={!valid}
          aria-describedby={`${id}-help`} onChange={(event) => onChange(event.target.value)}
          onBlur={normalize} />
      </label>
      <input className="value-slider" type="range" aria-label={`${label} slider`}
        min={min} max={rangeMax} step="1" value={valid ? Number(value) : min}
        disabled={disabled || !valid} onChange={(event) => onChange(Number(event.target.value))}
        aria-describedby={`${id}-help`} />
      <small id={`${id}-help`} className={!valid ? 'control-error' : 'control-help'}>
        {!valid ? `Enter a whole number from ${min} to ${inputMax}.`
          : rangeMax > max ? `Custom value; slider extended to ${rangeMax}.`
            : note || `${min}–${max}`}
      </small>
    </div>
  );
}
