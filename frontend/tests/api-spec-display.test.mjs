import assert from 'node:assert/strict';
import test from 'node:test';
import { apiOperationInputTypes, apiOperationResponses } from '../src/lib/api-spec-display.ts';

test('reads OpenAPI request, parameter, response types, and status codes without custom fields', () => {
  const operation = {
    requestBody: {
      content: {
        'application/json': { schema: { $ref: '#/components/schemas/CreateOrderRequest' } }
      }
    },
    parameters: [{ name: 'includeItems', schema: { type: 'boolean' } }],
    responses: {
      201: {
        content: {
          'application/json': { schema: { $ref: '#/components/schemas/Order' } }
        }
      },
      400: { description: 'Invalid request' },
      404: {
        content: {
          'application/json': { schema: { type: 'array', items: { $ref: '#/components/schemas/Error' } } }
        }
      }
    }
  };

  assert.deepEqual(apiOperationInputTypes(operation), ['CreateOrderRequest', 'includeItems: boolean']);
  assert.deepEqual(apiOperationResponses(operation), [
    { status: '201', outputType: 'Order' },
    { status: '400', outputType: null },
    { status: '404', outputType: 'Error[]' }
  ]);
});
