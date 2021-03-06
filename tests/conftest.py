import re
import types
import typing

import pytest
import trio

import datastore

DS = typing.TypeVar("DS", datastore.abc.BinaryDatastore, datastore.abc.ObjectDatastore[typing.Any])
ET = typing.TypeVar("ET", bytes, typing.List[object])
Query = typing.Any

exceptions_t = typing.Union[typing.Type[BaseException], typing.Tuple[typing.Type[BaseException], ...]]


class raises:
	"""Assert that a code block/function call raises ``expected_exception``
	either directly or as part of a :mod:`trio.MultiError` or raise a PyTest
	failure exception otherwise.
	
	This is the :mod:`trio.MultiError` compatible variant of :func:`pytest.raises`.
	"""
	
	def __init__(self, expected_exception: exceptions_t, *, match: str = None):
		self.expected_exception = expected_exception
		self.message = "DID NOT RAISE {}".format(expected_exception) 
		self.match_expr = match
	
	def __enter__(self) -> None:
		return None
	
	def __exit__(
			self, type: typing.Optional[typing.Type[BaseException]],
			value: typing.Optional[BaseException],
			tb: typing.Optional[types.TracebackType],
	) -> bool:
		__tracebackhide__ = True
		if type is None:
			pytest.fail(self.message)
		
		# Check if all received exceptions are of the correct type
		suppress_exception = True
		def validate_exc_type(exc: BaseException) -> None:
			__tracebackhide__ = True
			
			nonlocal suppress_exception
			if not isinstance(exc, self.expected_exception):
				suppress_exception = False
		trio.MultiError.filter(validate_exc_type, value)
		
		# Check if all received exceptions match the required pattern
		if self.match_expr is not None and suppress_exception:
			pat = re.compile(self.match_expr)
			def validate_exc_str(exc: BaseException) -> None:
				__tracebackhide__ = True
				
				if not pat.search(str(exc)):
					raise AssertionError(
						"Pattern {!r} not found in {!r}".format(self.match_expr, str(exc))
					)
			trio.MultiError.filter(validate_exc_str, value)
		
		return suppress_exception


class DatastoreTests(typing.Generic[DS, ET]):
	pkey: datastore.Key = datastore.Key('/dfadasfdsafdas/')
	stores: typing.List[DS]
	numelems: int
	length: int
	is_binary: bool
	test_put_new: bool
	test_rename: bool
	put_new_keys: typing.Dict[int, typing.List[datastore.Key]]
	
	#FIXME: For some reason `numelems` increases test runtime with at least n²
	def __init__(self, stores: typing.List[DS], test_put_new: bool = True,
	             test_rename: bool = True, numelems: int = 10):  # 1000):
		self.stores = stores
		self.numelems = numelems
		self.length = 0
		self.test_put_new = test_put_new
		self.test_rename = test_rename
		if test_put_new:
			self.put_new_keys = {idx: [] for idx in range(len(stores))}
		
		self.is_binary = isinstance(stores[0], datastore.abc.BinaryDatastore)
	
	
	def encode(self, value: object) -> ET:
		if self.is_binary:
			return str(value).encode()  # type: ignore[return-value]
		else:
			return [value]  # type: ignore[return-value]
	
	
	def check_length(self, size: int = None) -> None:
		for sn in self.stores:
			# Check number of elements on datastores that supports this (optional)
			try:
				assert len(sn) == self.length  # type: ignore
			except TypeError:
				pass
			
			# Check size of datastore in bytes if it supports this
			if size is not None:
				assert sn.datastore_stats().size in (size, None)
	
	
	async def subtest_remove_nonexistent(self) -> None:
		assert len(self.stores) > 0
		self.check_length(0)

		# ensure removing non-existent keys is ok.
		for value in range(0, self.numelems):
			key = self.pkey.child(str(value))
			for sn in self.stores:
				assert not await sn.contains(key)
				with pytest.raises(KeyError):
					await sn.delete(key)
				assert not await sn.contains(key)

		self.check_length(0)
	
	
	async def subtest_insert_elems(self) -> None:
		sn: DS
		key: datastore.Key
		value: int
		
		# insert numelems elems
		self.length += self.numelems
		for value in range(0, self.numelems):
			key = self.pkey.child(str(value))
			for sn in self.stores:
				assert not await sn.contains(key)
				with raises(KeyError):
					await sn.get(key)
				with raises(KeyError):
					await sn.get_all(key)
				with raises(KeyError):
					await sn.stat(key)
				
				with raises(KeyError):
					await sn.put(key, self.encode(value), create=False)  # type: ignore[arg-type]
				await sn.put(key, self.encode(value))  # type: ignore[arg-type]
				
				assert await sn.contains(key)
				assert await sn.get_all(key) == await (await sn.get(key)).collect() == self.encode(value)
				metadata = await sn.stat(key)
				if isinstance(metadata, datastore.util.StreamMetadata):
					assert metadata.size == len(self.encode(value))
				else:
					assert metadata.count == 1
		
		# reassure they're all there.
		self.check_length()
		
		if self.test_put_new:
			self.length += self.numelems
			# insert numelems unnamed keys
			for value in range(0, self.numelems):
				for sn_idx, sn in enumerate(self.stores):
					key = await sn.put_new(self.pkey, self.encode(value))  # type: ignore[arg-type]
					print(sn_idx, self.pkey, key)
					assert await sn.contains(key)
					assert await sn.get_all(key) == await (await sn.get(key)).collect() == self.encode(value)
					metadata = await sn.stat(key)
					if isinstance(metadata, datastore.util.StreamMetadata):
						assert metadata.size == len(self.encode(value))
					else:
						assert metadata.count == 1
					
					self.put_new_keys[sn_idx].append(key)
		
		# reassure they're all there.
		self.check_length()
		
		for value in range(0, self.numelems):
			key = self.pkey.child(str(value))
			for sn in self.stores:
				assert await sn.contains(key)
				assert await sn.get_all(key) == await (await sn.get(key)).collect() == self.encode(value)
		
		if self.test_put_new:
			for sn_idx, sn in enumerate(self.stores):
				for value, key in enumerate(self.put_new_keys[sn_idx]):
					assert await sn.contains(key)
					assert await sn.get_all(key) == await (await sn.get(key)).collect() == self.encode(value)
		
		self.check_length()
	
	
	@typing.no_type_check  #FIXME: This method is broken
	async def check_query(self, query, total, slice) -> datastore.Cursor:
		assert not self.is_binary  # Queries are only supported for object stores
		
		allitems: typing.List[int] = list(range(0, total))
		sn: datastore.abc.ObjectDatastore
		resultset: datastore.Cursor

		for sn in self.stores:
			try:
				contents = list(await sn.query(Query(self.pkey)))
				expected = contents[slice]
				resultset = await sn.query(query)
				result = list(resultset)

				# make sure everything is there.
				assert sorted(contents) == sorted(allitems)
				assert sorted(result) == sorted(expected)

				# TODO: should order be preserved?
				#assert result == expected

			except NotImplementedError:
				print('WARNING: %s does not implement query.' % sn)

		return resultset
	
	
	@typing.no_type_check  #FIXME: This method is broken
	async def subtest_queries(self) -> None:
		if self.is_binary:
			return  # Not supported on binary datastores
		
		sn: datastore.abc.ObjectDatastore
		value: int
		
		for value in range(0, self.numelems):
			key: datastore.Key = self.pkey.child(str(value))
			for sn in self.stores:
				await sn.put(key, value)

		k: datastore.Key = self.pkey
		n: int = int(self.numelems)

		await self.check_query(Query(k), n, slice(0, n))
		await self.check_query(Query(k, limit=n), n, slice(0, n))
		await self.check_query(Query(k, limit=n // 2), n, slice(0, n // 2))
		await self.check_query(Query(k, offset=n // 2), n, slice(n // 2, n))
		await self.check_query(Query(k, offset=n // 3, limit=n // 3), n, slice(n // 3, 2 * (n // 3)))
		del k
		del n
	
	
	async def subtest_rename(self) -> None:
		sn: DS
		value: int
		key_a: datastore.Key
		key_b: datastore.Key
		key_c: datastore.Key
		
		if not self.test_rename:
			return
		
		length: int = self.length
		self.check_length()
		
		for value in range(0, self.numelems):
			key_a = self.pkey.child(str(value))
			key_b = self.pkey.child(str(value) + "_" + str(value))
			for sn in self.stores:
				assert await sn.contains(key_a)
				assert not await sn.contains(key_b)
				
				# Rename to self must always succeed
				await sn.rename(key_a, key_a)
				
				assert await sn.contains(key_a)
				assert not await sn.contains(key_b)
				
				# Rename to something else
				await sn.rename(key_a, key_b)
				
				assert not await sn.contains(key_a)
				assert await sn.contains(key_b)
		
		self.check_length()
		assert self.length == length
		
		for value in range(0, self.numelems):
			key_a = self.pkey.child(str(value))
			key_b = self.pkey.child(str(value) + "_" + str(value))
			key_c = self.pkey.child(str(self.numelems - value - 1))
			for sn in self.stores:
				assert await sn.contains(key_b)
				assert not await sn.contains(key_c)
				
				# Rename to “opposite” name
				await sn.rename(key_b, key_c)
				
				assert not await sn.contains(key_b)
				assert await sn.contains(key_c)
				
				if value > (self.numelems / 2):
					assert await sn.contains(key_a)
					with raises(KeyError):
						await sn.rename(key_c, key_a, replace=False)
					assert await sn.contains(key_c)
		
		self.check_length()
		assert self.length == length
	
	
	async def subtest_update(self) -> None:
		sn: DS
		value: int
		
		length: int = self.length
		self.check_length()
		
		# change numelems elems
		for value in range(0, self.numelems):
			key: datastore.Key = self.pkey.child(str(value))
			for sn in self.stores:
				with raises(KeyError):
					await sn.put(key, self.encode(value + 1), replace=False)  # type: ignore[arg-type]
				assert await sn.contains(key)
				await sn.put(key, self.encode(value + 1))  # type: ignore[arg-type]
				assert await sn.contains(key)
				assert self.encode(value) != await sn.get_all(key)
				assert self.encode(value + 1) == await sn.get_all(key)
		
		if self.test_put_new:
			for sn_idx, sn in enumerate(self.stores):
				for value, key in enumerate(self.put_new_keys[sn_idx]):
					with raises(KeyError):
						await sn.put(key, self.encode(value + 1), replace=False)  # type: ignore[arg-type]
					assert await sn.contains(key)
					await sn.put(key, self.encode(value + 1))  # type: ignore[arg-type]
					assert await sn.contains(key)
					assert self.encode(value) != await sn.get_all(key)
					assert self.encode(value + 1) == await sn.get_all(key)
		
		self.check_length()
		assert self.length == length
	
	
	async def subtest_remove(self) -> None:
		sn: DS
		value: int
		
		# remove numelems elems
		for value in range(0, self.numelems):
			key: datastore.Key = self.pkey.child(str(value))
			for sn in self.stores:
				assert await sn.contains(key)
				await sn.delete(key)
				assert not await sn.contains(key)
		self.length -= self.numelems
		
		self.check_length()
		
		if self.test_put_new:
			for sn_idx, sn in enumerate(self.stores):
				for value, key in enumerate(self.put_new_keys[sn_idx]):
					assert await sn.contains(key)
					await sn.delete(key)
					assert not await sn.contains(key)
			self.length -= self.numelems
		
		self.check_length(0)
	
	
	async def subtest_simple(self) -> None:
		await self.subtest_remove_nonexistent()
		await self.subtest_insert_elems()
		#await self.subtest_queries()  #FIXME: Query is broken
		await self.subtest_rename()
		await self.subtest_update()
		await self.subtest_remove()


@pytest.fixture(name="DatastoreTests")
def return_datastore_tests() -> typing.Type[DatastoreTests[DS, ET]]:
	return DatastoreTests
