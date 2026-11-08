/**
 * Spoken pickup day/time -> a concrete New York datetime.
 * Dates are modelled as "naive NY wall-clock" JS Dates: the UTC fields of the
 * Date hold the NY wall-clock values (so getUTCHours() === the NY hour). This
 * keeps the arithmetic simple and deterministic without a TZ library.
 */

const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
const NUMS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7,
  eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12,
  fifteen: 15, thirty: 30, 'forty five': 45, 'forty-five': 45,
};
const PARTS: Record<string, number> = {
  morning: 9, noon: 12, midday: 12, afternoon: 14, evening: 18, tonight: 18,
};

/** Build a naive-NY Date from wall-clock components. */
export function naive(year: number, month1: number, day: number, hour = 0, minute = 0): Date {
  return new Date(Date.UTC(year, month1 - 1, day, hour, minute, 0, 0));
}

/** Python-style weekday: Monday=0 ... Sunday=6. */
function weekday(d: Date): number {
  return (d.getUTCDay() + 6) % 7;
}

/** Current NY wall-clock as a naive-NY Date. */
export function nowNY(): Date {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: 'numeric', second: 'numeric', hour12: false,
  }).formatToParts(new Date());
  const get = (t: string) => Number(parts.find((p) => p.type === t)!.value);
  let h = get('hour');
  if (h === 24) h = 0;
  return naive(get('year'), get('month'), get('day'), h, get('minute'));
}

export function parseDay(day: string, now: Date): Date | null {
  const text = day.toLowerCase();
  if (['today', 'tonight', 'this evening', 'this afternoon', 'this morning'].some((w) => text.includes(w))) {
    return new Date(now);
  }
  if (text.includes('tomorrow')) {
    return new Date(now.getTime() + 86_400_000);
  }
  for (let i = 0; i < DAYS.length; i += 1) {
    if (text.includes(DAYS[i])) {
      let ahead = (i - weekday(now) + 7) % 7;
      if (ahead === 0 && text.includes('next')) ahead = 7;
      return new Date(now.getTime() + ahead * 86_400_000);
    }
  }
  return null;
}

export function parseTime(time: string): [number, number] | null {
  let text = time.toLowerCase().replace(/\./g, '');
  const words = Object.keys(NUMS).sort((a, b) => b.length - a.length);
  for (const word of words) {
    text = text.replace(new RegExp(`\\b${word}\\b`, 'g'), String(NUMS[word]));
  }

  const m = text.match(/(?<!\d)(\d{1,2})(?:[:\s](\d{2}))?(?!\d)/);
  if (!m) {
    for (const [part, hour] of Object.entries(PARTS)) {
      if (text.includes(part)) return [hour, 0];
    }
    return null;
  }

  let hour = Number(m[1]);
  const minute = Number(m[2] ?? 0);
  if (hour > 23 || minute > 59) return null;
  if (hour <= 12) {
    const pm = /(?<![a-z])pm\b|afternoon|evening|tonight|night/.test(text);
    const am = /(?<![a-z])am\b|morning/.test(text);
    if (pm && hour < 12) hour += 12;
    else if (am && hour === 12) hour = 0;
    else if (!am && !pm && hour >= 1 && hour <= 7) hour += 12; // "after four" at a pharmacy means 4pm
  }
  return [hour, minute];
}

export function parsePickup(day: string, time: string, now: Date | null = null): Date | null {
  const base = parseDay(day, now ?? nowNY());
  const hm = parseTime(time);
  if (base === null || hm === null) return null;
  const when = naive(base.getUTCFullYear(), base.getUTCMonth() + 1, base.getUTCDate(), hm[0], hm[1]);
  return when.getTime() > (now ?? nowNY()).getTime() ? when : null;
}
