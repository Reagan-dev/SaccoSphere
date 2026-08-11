# Idempotent Task and Durable Webhook Acknowledgement Patterns

This document defines the patterns used in SaccoSphere for idempotent Celery tasks and durable webhook acknowledgements. These patterns ensure financial safety and data consistency when handling external payment callbacks.

## Idempotent Task Pattern

### Definition
An idempotent task is a Celery task that can be executed multiple times with the same input without causing duplicate side effects. This is critical for financial operations to prevent double-crediting members when callbacks are retried or delivered multiple times by external providers.

### Implementation in SaccoSphere

**File:** `payments/tasks.py`

**Key Function:** `_callback_already_processed(mpesa_transaction, transaction)`

**Detection Logic:**
```python
def _callback_already_processed(mpesa_transaction, transaction):
    return (
        mpesa_transaction.callback_received
        and transaction.status in {
            Transaction.Status.COMPLETED,
            Transaction.Status.FAILED,
            Transaction.Status.AMOUNT_MISMATCH,
        }
    )
```

**Fields Used for Duplicate Detection:**
- `MpesaTransaction.callback_received` (Boolean): Set to True when callback is first processed
- `Transaction.status` (Enum): Terminal states indicate processing is complete
- `checkout_request_id` (STK): Unique identifier from M-Pesa for STK push
- `conversation_id` (B2C): Unique identifier from M-Pesa for B2C disbursements

**Task Examples:**
- `process_stk_callback_task` (lines 19-92): Uses `checkout_request_id` to lookup transaction and check idempotency
- `process_b2c_callback_task` (lines 229-307): Uses `conversation_id` to lookup transaction and check idempotency

**Idempotency Flow:**
1. Task receives callback identifier (checkout_request_id or conversation_id)
2. Lookup MpesaTransaction by identifier with `select_for_update()` row lock
3. Check `_callback_already_processed()` - returns True if already processed
4. If True, log and return early without applying any changes
5. If False, proceed with normal processing and mark as complete

**Test Coverage:**
- `test_stk_callback_duplicate_delivery_does_not_double_credit` (payments/tests.py:631)
- `test_b2c_callback_duplicate_delivery_does_not_double_disburse` (payments/tests.py:763)

### When to Use This Pattern
Use this pattern for any task that:
- Processes external webhook callbacks
- Applies financial state changes (credits, debits, status updates)
- May be retried by the task queue or redelivered by external providers
- Has a unique identifier in the payload

### Pattern Template
```python
@shared_task(
    bind=True,
    name='your_app.tasks.process_callback',
    max_retries=3,
    default_retry_delay=60,
)
def process_callback_task(self, unique_identifier, payload):
    try:
        with db_transaction.atomic():
            # Lock the row to prevent race conditions
            record = YourModel.objects.select_for_update().get(
                unique_field=unique_identifier
            )
            
            # Check if already processed
            if _is_already_processed(record):
                logger.info(
                    'Callback already processed: identifier=%s',
                    unique_identifier
                )
                return True
            
            # Process the callback
            _apply_changes(record, payload)
            
            # Mark as processed
            record.processed = True
            record.save(update_fields=['processed'])
            
    except Exception as exc:
        countdown = 60 * 2 ** self.request.retries
        logger.warning(
            'Callback processing failed for identifier=%s. Retrying in %s seconds.',
            unique_identifier,
            countdown,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=countdown)
    
    return True
```

## Durable Webhook Acknowledgement Pattern

### Definition
Durable webhook acknowledgement ensures that when a webhook is received from an external provider (e.g., Safaricom M-Pesa), the raw payload is durably persisted to the database before acknowledging receipt to the provider. If the Celery task queue is temporarily unavailable, the payload is saved for later processing, and the provider receives a retry response (503) instead of a success response (200).

### Implementation in SaccoSphere

**File:** `payments/views.py`

**Key Components:**

**1. Broker Connection Error Detection:**
```python
BROKER_CONNECTION_ERRORS = (
    AmqpConnectionError,
    KombuOperationalError,
    RedisConnectionError,
    RedisTimeoutError,
)
```

**2. Fallback Persistence Function:**
```python
def _persist_mpesa_enqueue_failure(
    *,
    callback_body,
    error,
    callback_type,
    mpesa_transaction=None,
):
    transaction = None
    if mpesa_transaction is not None:
        transaction = mpesa_transaction.transaction

    return Callback.objects.create(
        transaction=transaction,
        provider=_get_mpesa_provider_record(),
        raw_payload={
            'callback_type': callback_type,
            'payload': callback_body,
        },
        processed=False,
        processing_error=str(error),
    )
```

**3. Retry Response Generator:**
```python
def _retry_mpesa_response(result_desc='Temporary processing unavailable'):
    return JsonResponse(
        {'ResultCode': 1, 'ResultDesc': result_desc},
        status=503,
    )
```

**4. View Implementation (STK Example):**
```python
try:
    process_stk_callback_task.delay(
        checkout_request_id,
        result_code,
        callback_body,
    )
except BROKER_CONNECTION_ERRORS as exc:
    logger.error(
        'M-Pesa STK callback task enqueue error: %s',
        exc,
        exc_info=True,
    )
    _persist_mpesa_enqueue_failure(
        callback_body=callback_body,
        error=exc,
        callback_type='STK',
        mpesa_transaction=mpesa_transaction,
    )
    _clear_mpesa_replay_marker(checkout_request_id)
    return _retry_mpesa_response()

logger.info('M-Pesa STK callback enqueued: %s', checkout_request_id)
return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})
```

**Table Used for Fallback Storage:**
- `Callback` model (payments/models.py:164-210)
- `raw_payload` (JSONField): Stores the complete callback payload
- `processed` (Boolean): False when enqueued for later processing
- `processing_error` (TextField): Stores the enqueue error for debugging
- `transaction` (ForeignKey): Links to related Transaction if known

**View Examples:**
- `MPesaSTKCallbackView.post` (payments/views.py:677-772)
- `B2CCallbackView.post` (payments/views.py:840-935)

**Test Coverage:**
- `test_stk_enqueue_failure_is_saved_and_returns_retry` (payments/tests.py:315)
- `test_b2c_enqueue_failure_is_saved_and_returns_retry` (payments/tests.py:363)

### When to Use This Pattern
Use this pattern for any webhook endpoint that:
- Receives callbacks from external payment providers
- Enqueues asynchronous processing via Celery
- Must not lose callbacks even if the task queue is temporarily down
- Needs to signal providers to retry on temporary failures

### Pattern Template
```python
from amqp.exceptions import ConnectionError as AmqpConnectionError
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

BROKER_CONNECTION_ERRORS = (
    AmqpConnectionError,
    KombuOperationalError,
    RedisConnectionError,
    RedisTimeoutError,
)

class WebhookCallbackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    
    def post(self, request):
        # Validate and verify webhook
        if not self._verify_webhook(request):
            return Response({'detail': 'Forbidden'}, status=403)
        
        # Extract unique identifier and payload
        identifier = self._extract_identifier(request.data)
        payload = request.data
        
        try:
            # Attempt to enqueue task
            process_callback_task.delay(identifier, payload)
        except BROKER_CONNECTION_ERRORS as exc:
            # Broker unavailable - persist for retry
            self._persist_fallback(identifier, payload, exc)
            # Return 503 to tell provider to retry
            return self._retry_response()
        
        # Successfully enqueued - return 200
        return Response({'received': True}, status=200)
    
    def _persist_fallback(self, identifier, payload, error):
        Callback.objects.create(
            raw_payload=payload,
            processed=False,
            processing_error=str(error),
        )
    
    def _retry_response(self):
        return Response(
            {'detail': 'Temporary processing unavailable'},
            status=503,
        )
```

## Summary of Key Principles

1. **Never acknowledge success before durable storage:** Always persist the payload before returning 200 to the provider.
2. **Use row locks for idempotency checks:** `select_for_update()` prevents race conditions when checking if a callback was already processed.
3. **Return 503 on transient failures:** This tells providers to retry, while 200 tells them the callback was accepted.
4. **Log all failures:** Both idempotency skips and enqueue failures must be logged for debugging.
5. **Test duplicate delivery:** Always include tests that call the task twice to prove idempotency.
6. **Distinguish transient from permanent errors:** Only retry on connection/broker errors, not on data validation errors.
