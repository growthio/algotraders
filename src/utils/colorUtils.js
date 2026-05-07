const CALL_OI_COLORS = ['#D99695', '#E6B9B8', '#F2DCDB'];
const PUT_OI_COLORS = ['#C3D79B', '#D7E4BD', '#EBF1DE'];

export function getTop3Color(colorScheme, rank) {
  const colors = colorScheme === 'call' ? CALL_OI_COLORS : PUT_OI_COLORS;
  if (rank < 1 || rank > 3) return null;
  return { background: colors[rank - 1], color: '#000000', fontWeight: 'bold' };
}

export function getIVStyle(value) {
  if (value > 16) return { background: '#F2DCDB', color: '#9C0006' };
  return null;
}

export function getPCRStyle(pcr) {
  if (pcr > 1.1) return { background: '#C6EFCE', color: '#006100' };
  if (pcr < 0.9) return { background: '#FFC7CE', color: '#9C0006' };
  return { background: '#FFEB9C', color: '#9C5700' };
}

export function getRatioStyle(ratio) {
  if (ratio > 0.93) return { background: '#FFC7CE', color: '#9C0006' };
  if (ratio > 0.83) return { background: '#FFE4E1', color: '#9C0006' };
  if (ratio > 0.75) return { background: '#FFEB9C', color: '#9C5700' };
  return { background: '#C6EFCE', color: '#006100' };
}

function hexToRgb(hex) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return [r, g, b];
}

function rgbToHex(r, g, b) {
  return (
    '#' +
    [r, g, b]
      .map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0'))
      .join('')
  );
}

function interpolateColor(c1, c2, t) {
  const [r1, g1, b1] = hexToRgb(c1);
  const [r2, g2, b2] = hexToRgb(c2);
  return rgbToHex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t);
}

export function getPctChngOIColorScale(value, min, max) {
  const GREEN = '#63BE7B';
  const YELLOW = '#FFEB84';
  const RED = '#F8696B';

  if (min === max) return { background: YELLOW, color: '#000000' };

  let bg;
  if (value <= 0) {
    const t = min === 0 ? 0 : (value - min) / (0 - min);
    bg = interpolateColor(GREEN, YELLOW, Math.max(0, Math.min(1, t)));
  } else {
    const t = max === 0 ? 0 : (value - 0) / (max - 0);
    bg = interpolateColor(YELLOW, RED, Math.max(0, Math.min(1, t)));
  }

  const [r, g, b] = hexToRgb(bg);
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  const textColor = luminance > 0.5 ? '#000000' : '#FFFFFF';

  return { background: bg, color: textColor };
}
