export function getTop3ByField(dataSlice, fieldFn) {
  return [...dataSlice]
    .sort((a, b) => fieldFn(b) - fieldFn(a))
    .slice(0, 3);
}

function computeRatios(top3, fieldFn) {
  return {
    r2_1: fieldFn(top3[0]) > 0 ? fieldFn(top3[1]) / fieldFn(top3[0]) : 0,
    r3_2: fieldFn(top3[1]) > 0 ? fieldFn(top3[2]) / fieldFn(top3[1]) : 0,
  };
}

function computeDiffs(top3, fieldFn) {
  return {
    top1_2: (fieldFn(top3[0]) ?? 0) - (fieldFn(top3[1]) ?? 0),
    top2_3: (fieldFn(top3[1]) ?? 0) - (fieldFn(top3[2]) ?? 0),
  };
}

export function computeSummary(rows, spotPrice) {
  const indices11to30 = rows.slice(11, 31);
  const indices18to23 = rows.slice(18, 24);

  const totalCallChngOI = indices11to30.reduce((s, r) => s + r.call.chngOI, 0);
  const totalPutChngOI = indices11to30.reduce((s, r) => s + r.put.chngOI, 0);
  const totalCallOI = indices11to30.reduce((s, r) => s + r.call.oi, 0);
  const totalPutOI = indices11to30.reduce((s, r) => s + r.put.oi, 0);
  const nearCallChngOI = indices18to23.reduce((s, r) => s + r.call.chngOI, 0);
  const nearPutChngOI = indices18to23.reduce((s, r) => s + r.put.chngOI, 0);
  const totalCallVol = indices11to30.reduce((s, r) => s + r.call.volume, 0);
  const totalPutVol = indices11to30.reduce((s, r) => s + r.put.volume, 0);
  const pcr = totalCallOI > 0 ? totalPutOI / totalCallOI : 0;

  const atmIndex = rows.findIndex((r) => r.strike === spotPrice);

  return {
    totalCallChngOI,
    totalPutChngOI,
    totalCallOI,
    totalPutOI,
    nearCallChngOI,
    nearPutChngOI,
    totalCallVol,
    totalPutVol,
    pcr,
    atmIndex,
  };
}

export function computeOCProfile(rows, spotPrice) {
  const atmIndex = rows.findIndex((r) => r.strike === spotPrice);
  const otmCalls = rows.slice(atmIndex + 1);
  const otmPuts = rows.slice(0, atmIndex);

  const callTop3ByOI = getTop3ByField(otmCalls, (r) => r.call.oi);
  const callTop3ByChngOI = getTop3ByField(otmCalls, (r) => r.call.chngOI);
  const callTop3ByVol = getTop3ByField(otmCalls, (r) => r.call.volume);

  const putTop3ByOI = getTop3ByField(otmPuts, (r) => r.put.oi);
  const putTop3ByChngOI = getTop3ByField(otmPuts, (r) => r.put.chngOI);
  const putTop3ByVol = getTop3ByField(otmPuts, (r) => r.put.volume);

  const resistance = [0, 1, 2].map((i) =>
    Math.min(
      callTop3ByOI[i]?.strike ?? Infinity,
      callTop3ByChngOI[i]?.strike ?? Infinity,
      callTop3ByVol[i]?.strike ?? Infinity
    )
  );

  const support = [0, 1, 2].map((i) =>
    Math.max(
      putTop3ByOI[i]?.strike ?? 0,
      putTop3ByChngOI[i]?.strike ?? 0,
      putTop3ByVol[i]?.strike ?? 0
    )
  );

  return {
    callTop3ByOI,
    callTop3ByChngOI,
    callTop3ByVol,
    putTop3ByOI,
    putTop3ByChngOI,
    putTop3ByVol,
    resistance,
    support,
    callOIRatios: computeRatios(callTop3ByOI, (r) => r?.call.oi ?? 0),
    callChngOIRatios: computeRatios(callTop3ByChngOI, (r) => r?.call.chngOI ?? 0),
    callVolRatios: computeRatios(callTop3ByVol, (r) => r?.call.volume ?? 0),
    putOIRatios: computeRatios(putTop3ByOI, (r) => r?.put.oi ?? 0),
    putChngOIRatios: computeRatios(putTop3ByChngOI, (r) => r?.put.chngOI ?? 0),
    putVolRatios: computeRatios(putTop3ByVol, (r) => r?.put.volume ?? 0),
    callOIDiff: computeDiffs(callTop3ByOI, (r) => r?.call.oi ?? 0),
    callVolDiff: computeDiffs(callTop3ByVol, (r) => r?.call.volume ?? 0),
    putOIDiff: computeDiffs(putTop3ByOI, (r) => r?.put.oi ?? 0),
    putVolDiff: computeDiffs(putTop3ByVol, (r) => r?.put.volume ?? 0),
  };
}

export function formatIndian(num) {
  if (num === null || num === undefined) return '-';
  return Math.abs(num).toLocaleString('en-IN');
}

export function formatIV(val) {
  if (val === null || val === undefined) return '-';
  return val.toFixed(2) + '%';
}

export function formatLTP(val) {
  if (val === null || val === undefined) return '-';
  return val.toFixed(2);
}

export function formatPct(val) {
  if (val === null || val === undefined) return '-';
  return (val >= 0 ? '+' : '') + val.toFixed(2) + '%';
}
