/** Responsibility: Verify fixed SGD conversion and stable monetary marker sizing.
 * Implementation: Independent published cross-rate examples, supported-currency coverage, malformed inputs and extreme source values test the pure production functions.
 * Relationships: world-currency.js is shared by labels, map and legend; these checks require no browser, database or network.
 * Directory: None.
 * Variable index: supported lists the backend insight currency contract; record verifies no source mutation; amounts/diameters exercise monotonic sizing over operational ranges.
 */
import assert from 'node:assert/strict';
import { FX_REFERENCE, SGD_RATES, amountInSgd, bubbleDiameter } from '../frontend/assets/world-currency.js';

const supported = ['CNY', 'USD', 'EUR', 'GBP', 'JPY', 'KRW', 'SGD', 'TWD', 'HKD', 'INR', 'CAD', 'AUD', 'CHF'];
assert.deepEqual(Object.keys(SGD_RATES).sort(), supported.sort());
assert.equal(FX_REFERENCE.date, '2026-09-21');
assert.ok(Object.isFrozen(SGD_RATES));
for (const currency of supported) assert.ok(amountInSgd({ amount: '1', currency }) > 0);
// Independent reference: EUR 100 = SGD 146.47; USD 114.90 and TWD 3648.9942 have the same approximate value on the fixed date.
assert.equal(amountInSgd({ amount: '100', currency: 'EUR' }), 146.47);
assert.ok(Math.abs(amountInSgd({ amount: '114.90', currency: 'USD' }) - 146.47) < 0.00001);
assert.ok(Math.abs(amountInSgd({ amount: '3648.9942', currency: 'TWD' }) - 146.47) < 0.0001);
assert.equal(amountInSgd({ amount: '0.000000', currency: 'SGD' }), 0);
assert.equal(amountInSgd({ amount: null, currency: '' }), null);
assert.equal(amountInSgd({}), null);
for (const amount of ['', ' ', '-1', '1e5', 'NaN', 'Infinity', 100]) assert.throws(() => amountInSgd({ amount, currency: 'USD' }), TypeError);
assert.throws(() => amountInSgd({ amount: '1', currency: 'XYZ' }), RangeError);
assert.throws(() => amountInSgd({ amount: '1', currency: 'toString' }), RangeError);
const record = Object.freeze({ amount: '999999999999999999999999.123456', currency: 'EUR', amount_qualifier: 'up_to' });
assert.equal(bubbleDiameter(amountInSgd(record)), 80);
assert.equal(record.amount, '999999999999999999999999.123456');
assert.equal(record.currency, 'EUR');
assert.equal(bubbleDiameter(null), 14);
assert.equal(bubbleDiameter(0), 18);
const amounts = [0, 1, 100, 1000, 1000000, 100000000, 1000000000];
const diameters = amounts.map(bubbleDiameter);
assert.ok(diameters.every((value, index) => !index || value > diameters[index - 1]));
assert.ok(Math.abs(bubbleDiameter(amountInSgd({ amount: '100', currency: 'EUR' })) - bubbleDiameter(146.47)) < 1e-9);
for (const value of [-1, NaN, Infinity, undefined]) assert.throws(() => bubbleDiameter(value), RangeError);
console.log('Fixed SGD rates: currency coverage, conversion direction, reference date, zero/unknown, invalid inputs, source preservation and stable logarithmic sizes passed.');
