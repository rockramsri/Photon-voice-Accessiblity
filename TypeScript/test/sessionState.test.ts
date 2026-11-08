import { describe, it, expect } from 'vitest';
import { type Candidate, ResolveError, SessionState } from '../src/voice/sessionState.js';

function makeState(): SessionState {
  const s = new SessionState({ orderId: 'ord_1', patientId: 'pat_1', patientToken: 't', orgName: 'NYU' });
  s.add(
    [
      { pharmacy_id: 'p1', name: 'Avenue O Pharmacy', source: 'nearby', street: '26 AVENUE O' },
      { pharmacy_id: 'p2', name: 'Sky Pharmacy', source: 'nearby' },
      { pharmacy_id: 'p3', name: 'CVS Pharmacy', source: 'nearby', street: '1 KINGS HWY' },
      { pharmacy_id: 'p4', name: 'Amazon Pharmacy', source: 'mailorder' },
    ] as Candidate[],
    ['p1', 'p2', 'p3'],
  );
  return s;
}

describe('ordinals use last spoken options', () => {
  it.each([
    ['the first one', 'p1'],
    ['option two', 'p2'],
    ['the third', 'p3'],
    ['the last one', 'p3'],
  ])('%s -> %s', (ref, expected) => {
    expect(makeState().resolve(ref).pharmacy_id).toBe(expected);
  });
});

describe('exact and fuzzy names', () => {
  it.each([
    ['Avenue O Pharmacy', 'p1'],
    ['sky', 'p2'],
    ['amazon', 'p4'],
    ['Avenue Oh pharmacy', 'p1'],
  ])('%s -> %s', (ref, expected) => {
    expect(makeState().resolve(ref).pharmacy_id).toBe(expected);
  });
});

describe('merge behaviour', () => {
  it('new search keeps old pharmacies but moves ordinals', () => {
    const s = makeState();
    s.add([{ pharmacy_id: 'p9', name: 'Walgreens', source: 'nearby' }] as Candidate[], ['p9']);
    expect(s.resolve('option one').pharmacy_id).toBe('p9');
    expect(s.resolve('Sky Pharmacy').pharmacy_id).toBe('p2');
  });

  it('two CVS locations are ambiguous', () => {
    const s = makeState();
    s.add([{ pharmacy_id: 'p5', name: 'CVS Pharmacy', source: 'nearby', street: '9 OCEAN PKWY' }] as Candidate[]);
    expect(() => s.resolve('CVS')).toThrow(ResolveError);
    expect(() => s.resolve('CVS')).toThrow(/KINGS HWY|OCEAN PKWY/);
  });

  it('unknown name not found', () => {
    expect(() => makeState().resolve('Duane Reade')).toThrow(/Couldn't find/);
  });

  it('current pharmacy stays current after merge', () => {
    const s = makeState();
    s.add([{ pharmacy_id: 'p7', name: 'Optumrx', source: 'current' }] as Candidate[]);
    s.add([{ pharmacy_id: 'p7', name: 'Optumrx', source: 'nearby' }] as Candidate[]);
    expect(s.candidates.get('p7')!.source).toBe('current');
  });
});
