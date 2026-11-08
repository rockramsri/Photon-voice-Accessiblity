import { describe, it, expect } from 'vitest';
import {
  moneyToWords, rankNearby, sanitizeForVoice, spokenClock, spokenHours, spokenMed, spokenStrength,
} from '../src/voice/shapeContext.js';

function place(
  pid: string,
  dist: number,
  isOpen: boolean,
  price: number | null,
  close = '2026-11-07T23:00:00Z',
  h24 = false,
): Record<string, any> {
  const closeEv = h24 ? { type: '24hr' } : { type: 'close', datetime: close };
  return {
    id: pid, name: pid, distance: dist, isOpen, price,
    nextEvents: { open: { type: 'open', datetime: '2026-11-08T14:00:00Z' }, close: closeEv },
  };
}

describe('rankNearby', () => {
  it('balances open, price and hours', () => {
    const ranked = rankNearby([
      place('closed_closest', 0.1, false, 5.0),
      place('open_near', 0.3, true, 15.0),
      place('open_cheap', 0.9, true, 9.0),
      place('cheap_but_far', 3.0, true, 1.0),
      place('all_night', 1.4, true, 30.0, undefined, true),
    ]);
    expect(ranked.slice(0, 3).map((p) => [p.id, p.why])).toEqual([
      ['open_near', 'the closest of the ones open now'],
      ['open_cheap', 'the cheapest of the ones open now'],
      ['all_night', 'the latest-closing of the ones open now'],
    ]);
    expect(ranked[3].id).toBe('closed_closest');
  });

  it('one store winning everything reads cleanly', () => {
    const ranked = rankNearby([place('a', 0.2, true, 5.0, undefined, true), place('b', 0.5, true, 9.0)]);
    expect(ranked[0].why).toBe('the closest and cheapest and latest-closing of the ones open now');
    expect(ranked[1].why).toBeUndefined();
  });

  it('nothing open falls back to closest', () => {
    const ranked = rankNearby([place('b', 0.5, false, 5.0), place('a', 0.1, false, 5.0)]);
    expect(ranked.map((p) => p.id)).toEqual(['a', 'b']);
    expect(ranked[0].why).toBeUndefined();
  });

  it('closed store says when it opens', () => {
    expect(spokenHours(place('x', 0.1, false, 5.0))).toBe('closed now, opens nine in the morning');
  });
});

const REAL_FAQ =
  'If the prescription has been confirmed, you need to contact Photon Support directly ' +
  'via text at [513-866-3212](sms:513-866-3212) for assistance.';

describe('sanitizeForVoice', () => {
  it('turns a markdown-link phone into spoken digits', () => {
    const out = sanitizeForVoice(REAL_FAQ);
    expect(out).not.toContain('[');
    expect(out).not.toContain('sms:');
    expect(out).toContain('five one three, eight six six, three two one two');
  });

  it('urls, money and markup', () => {
    expect(sanitizeForVoice('Visit https://orders.neutron.health/?token=x')).toBe('Visit orders dot neutron dot health');
    expect(sanitizeForVoice('**about $3.76**')).toBe('about three dollars seventy-six');
    expect(sanitizeForVoice('- one\n- two')).toBe('one two');
  });

  it('expressive tags survive', () => {
    expect(sanitizeForVoice('<emotion value="calm"/>Hi there')).toBe('<emotion value="calm"/>Hi there');
  });
});

describe('words + clock + strength + med', () => {
  it('money words', () => {
    expect(moneyToWords(12.04)).toBe('twelve dollars four');
    expect(moneyToWords(1)).toBe('one dollar');
    expect(moneyToWords(129.99)).toBe('one hundred twenty-nine dollars ninety-nine');
  });

  it('clock and strength', () => {
    expect(spokenClock(new Date(Date.UTC(2026, 0, 1, 17, 0)))).toBe('five tonight');
    expect(spokenClock(new Date(Date.UTC(2026, 0, 1, 9, 30)))).toBe('nine thirty in the morning');
    expect(spokenStrength('Azithromycin 100 MG/5ML')).toBe('Azithromycin 100 milligrams per 5 milliliters');
  });

  it('spoken med strips catalog noise', () => {
    expect(spokenMed('metFORMIN HCl ER (MOD) Oral Tablet Extended Release 24 Hour 1000 MG')).toEqual([
      'metformin extended-release, one thousand milligrams, tablets', 'metformin',
    ]);
    expect(spokenMed('Azithromycin Oral Suspension Reconstituted 100 MG/5ML')).toEqual([
      'azithromycin, one hundred milligrams per five milliliters, suspension', 'azithromycin',
    ]);
    expect(spokenMed(null)).toEqual(['your prescription', 'prescription']);
  });
});
