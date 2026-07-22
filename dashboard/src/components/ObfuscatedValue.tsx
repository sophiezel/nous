"use client";

import { useEffect, useRef } from "react";

interface ObfuscatedValueProps {
  value: string | number;
  /** CSS color for the displayed value */
  color?: string;
  /** Additional CSS classes */
  className?: string;
  /** Font size class, e.g. "text-2xl" */
  fontSize?: string;
  /** Font weight class */
  fontWeight?: string;
  /** Screen reader label */
  ariaLabel?: string;
}

/**
 * Renders a value via CSS ::after pseudo-element.
 * The value does NOT exist in DOM text nodes — HTML scrapers see nothing.
 * Only browsers rendering CSS will display the number.
 */
export function ObfuscatedValue({
  value,
  color,
  className = "",
  fontSize = "text-2xl",
  fontWeight = "font-bold",
  ariaLabel,
}: ObfuscatedValueProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const styleRef = useRef<HTMLStyleElement | null>(null);

  useEffect(() => {
    const id = `ov-${Math.random().toString(36).slice(2, 10)}`;
    const el = ref.current;
    if (!el) return;
    el.className = id;

    const style = document.createElement("style");
    const display = String(value);
    const colorCss = color ? `color: ${color};` : "";
    style.textContent = `.${id}::after { content: "${display}"; ${colorCss} }`;
    document.head.appendChild(style);
    styleRef.current = style;

    return () => {
      if (styleRef.current) styleRef.current.remove();
    };
  }, [value, color]);

  return (
    <span
      ref={ref}
      aria-label={ariaLabel || String(value)}
      className={`inline-block tabular-nums ${fontSize} ${fontWeight} ${className}`}
    />
  );
}

/**
 * Simpler variant: renders a number with randomized CSS order segments.
 * Useful for multi-digit numbers — DOM has digits in random order,
 * CSS flexbox 'order' restores correct display.
 */
export function ObfuscatedNumber({
  value,
  color,
  className = "",
  fontSize = "text-2xl",
  fontWeight = "font-bold",
}: ObfuscatedValueProps) {
  // For numbers < 100, use simple pseudo-element approach
  // For larger/more complex, use segment shuffling
  const str = String(value);
  const segments = str.split("").map((char, i) => ({ char, order: i }));

  // Shuffle while keeping track of correct position
  if (segments.length > 2) {
    for (let i = segments.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [segments[i], segments[j]] = [segments[j], segments[i]];
    }
  }

  if (segments.length <= 2) {
    // Use simple CSS ::after for 1-2 chars
    return (
      <ObfuscatedValue
        value={value}
        color={color}
        className={className}
        fontSize={fontSize}
        fontWeight={fontWeight}
      />
    );
  }

  return (
    <span
      className={`inline-flex ${fontSize} ${fontWeight} tabular-nums ${className}`}
      aria-label={String(value)}
    >
      {segments.map((seg, i) => (
        <span
          key={i}
          style={{ order: seg.order, color }}
          className="inline-block"
        >
          {seg.char}
        </span>
      ))}
    </span>
  );
}
