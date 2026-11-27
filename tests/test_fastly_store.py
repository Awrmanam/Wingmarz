"""Regression tests for the isolated Fastly stock and warranty ledger."""
import asyncio
import os
import sqlite3

import pytest
from cryptography.fernet import Fernet

from fastly_store_service import FastlyStore, FastlyStoreError


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTLY_STORE_FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FASTLY_STORE_ENABLED", "1")
    return FastlyStore(str(tmp_path / "fastly.sqlite"))


async def _product(store, *, warranty=0, replacements=0, price=120000):
    return await store.add_product(
        "فستلی تست" + str(warranty),price,warranty,replacements,
        "استفاده منصفانه؛ ضمانت طبق شرایط سفارش.",999,
    )


async def _paid_order(store, buyer, product, qty=1):
    ident=await store.make_order(buyer,product,qty,"first")
    assert await store.submit_receipt(ident,buyer,"fake-file-id")
    return ident


@pytest.mark.asyncio
async def test_encrypted_stock_and_duplicate_rejection(store):
    pid=await _product(store)
    assert (await store.add_stock(pid,[("one@example.test","password-AAA"),("one@example.test","password-BBB")],999))==(1,1)
    raw=(store.db_path and open(store.db_path,"rb").read())
    assert b"password-AAA" not in raw and b"one@example.test" not in raw
    product=await store.product(pid)
    assert product["stock"]==1


@pytest.mark.asyncio
async def test_two_concurrent_orders_can_never_sell_same_inventory(store):
    pid=await _product(store)
    assert (await store.add_stock(pid,[("a@test.invalid","a-secret"),("b@test.invalid","b-secret")],999))[0]==2
    a=await _paid_order(store,101,pid,2)
    b=await _paid_order(store,102,pid,2)
    results=await asyncio.gather(store.approve(a,999),store.approve(b,999))
    assert sorted(results)==["allocated","out_of_stock"]
    assert (await store.stats())["sold_units"]==2
    winner=a if (await store.order(a))["state"]=="allocated" else b
    buyer=101 if winner==a else 102
    assert await store.approve(winner,999)=="already_allocated"
    with pytest.raises(FastlyStoreError):
        await store.reveal(winner,buyer)
    assert not await store.accept_rules(winner,999)
    assert await store.accept_rules(winner,buyer)
    credentials=await store.reveal(winner,buyer)
    assert len(credentials)==len({x["login"] for x in credentials})==2
    with pytest.raises(FastlyStoreError):
        await store.reveal(winner,999)


@pytest.mark.asyncio
async def test_repeat_checkout_idempotent_and_price_snapshot(store):
    pid=await _product(store,price=500000)
    await store.add_stock(pid,[("a@test.invalid","secret1"),("b@test.invalid","secret2")],999)
    first=await store.make_order(101,pid,1,"first")
    duplicate=await store.make_order(101,pid,1,"first")
    assert duplicate==first
    await store.edit_product(pid,"price_toman",750000,999)
    old=await store.order(first)
    assert old["total_toman"]==500000
    assert (await store.make_order(101,pid,1,"extra"))!=first


@pytest.mark.asyncio
async def test_claim_entitlement_chain_and_snapshot(store):
    pid=await _product(store,warranty=3,replacements=1)
    await store.add_stock(pid,[("a@test.invalid","oldsecret"),("b@test.invalid","newsecret")],999)
    oid=await _paid_order(store,1001,pid)
    assert await store.approve(oid,999)=="allocated"
    item=(await store.claimable_items(1001))[0]
    claim=await store.claim(1001,item["id"],"login")
    with pytest.raises(FastlyStoreError):
        await store.claim(1001,item["id"],"blocked")
    assert await store.decide_claim(claim,999,True)=="replaced"
    assert await store.decide_claim(claim,999,True)=="replaced"
    assert await store.accept_rules(oid,1001)
    credentials=await store.reveal(oid,1001)
    assert len(credentials)==1 and credentials[0]["login"]=="b@test.invalid"
    assert len(await store.claimable_items(1001))==1
    new_item=(await store.claimable_items(1001))[0]
    with pytest.raises(FastlyStoreError):
        await store.claim(1001,new_item["id"],"login")
    with pytest.raises(FastlyStoreError):
        await store.claim(999,new_item["id"],"login")
    assert (await store.stats())["replacements"]==1


@pytest.mark.asyncio
async def test_rejected_receipt_never_allocates(store):
    pid=await _product(store)
    await store.add_stock(pid,[("a@test.invalid","secret")],999)
    oid=await _paid_order(store,1001,pid)
    assert await store.reject(oid,999)
    with pytest.raises(FastlyStoreError):
        await store.approve(oid,999)
    assert (await store.product(pid))["stock"]==1
