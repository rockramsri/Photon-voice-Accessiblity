import { describe, it, expect } from 'vitest';
import { naive, parsePickup, parseTime } from '../src/voice/spokenTime.js';

const NOW = naive(2026, 11, 7, 10, 0); // Saturday 10am NY

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

describe('parseTime', () => {
  it.each([
    ['after four', [16, 0]],
    ['4 pm', [16, 0]],
    ['nine thirty in the morning', [9, 30]],
    ['noon', [12, 0]],
    ['this evening', [18, 0]],
    ['10am', [10, 0]],
    ['5:45', [17, 45]],
  ])('%s -> %j', (text, expected) => {
    expect(parseTime(text as string)).toEqual(expected);
  });
});

describe('parsePickup', () => {
  it('tomorrow after four', () => {
    const when = parsePickup('tomorrow', 'after four', NOW)!;
    expect([when.getUTCDate(), when.getUTCHours()]).toEqual([8, 16]);
  });

  it('weekday rolls forward', () => {
    const when = parsePickup('monday', 'noon', NOW)!;
    expect(WEEKDAYS[when.getUTCDay()]).toBe('Monday');
    expect(when.getUTCDate()).toBe(9);
  });

  it('past or unclear returns null', () => {
    expect(parsePickup('today', 'nine in the morning', NOW)).toBeNull();
    expect(parsePickup('someday', 'four', NOW)).toBeNull();
    expect(parsePickup('tomorrow', 'whenever', NOW)).toBeNull();
  });
});
