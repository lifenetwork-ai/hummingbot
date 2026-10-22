"""Spawned synthetic worker: only public registration and signed bounded RPC cross IPC."""
import hashlib

from hummingbot.strategy_v2.life_liquidity.storage.generation_access import BrokerIdentity, BrokerReply


def run(pipe, public_identity, public_registration):
    private = BrokerIdentity.create(public_identity, public_registration)
    pipe.send(private.registration)
    try:
        while pipe.poll(10):
            command = pipe.recv()
            if command is None:
                return
            action, payload = command
            try:
                call = private.call(action, payload)
            except Exception as error:
                pipe.send(('local-denied', type(error).__name__))
                continue
            pipe.send(call)
            if not pipe.poll(10):
                raise TimeoutError('bounded broker fixture response')
            reply = pipe.recv()
            if type(reply) is not BrokerReply:
                pipe.send(('denied', None))
            else:
                # Only the worker has its signing key; return no secret for assertions.
                result = reply.body['result']
                pipe.send(('fetched', hashlib.sha256(result['api_key'].encode()).hexdigest()))
    finally:
        pipe.close()
