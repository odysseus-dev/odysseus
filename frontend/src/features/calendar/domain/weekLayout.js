export function layoutTimedEvents(intervals, { gridStart, gridEnd, hourPx }) {
  const items = intervals.map(({ ev, startMinute, endMinute }) => {
    const end = endMinute ?? (startMinute + 60);
    const start = Math.max(gridStart, startMinute);
    const finish = Math.min(gridEnd, Math.max(end, start + 15));
    const top = (start - gridStart) * (hourPx / 60);
    const height = Math.max(18, (finish - start) * (hourPx / 60));
    return { ev, top, height, start: top, end: top + height, lane: 0, laneCount: 1 };
  }).sort((a, b) => (a.start - b.start) || (a.end - b.end));

  const clusters = [];
  let cluster = null;
  for (const item of items) {
    if (!cluster || item.start >= cluster.end) {
      cluster = { end: item.end, items: [item] };
      clusters.push(cluster);
    } else {
      cluster.items.push(item);
      cluster.end = Math.max(cluster.end, item.end);
    }
  }

  for (const group of clusters) {
    const laneEnds = [];
    for (const item of group.items) {
      let lane = laneEnds.findIndex(end => end <= item.start);
      if (lane < 0) lane = laneEnds.length;
      laneEnds[lane] = item.end;
      item.lane = lane;
    }
    const laneCount = Math.max(1, laneEnds.length);
    for (const item of group.items) item.laneCount = laneCount;
  }

  return items;
}
