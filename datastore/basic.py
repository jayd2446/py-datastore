
from query import Cursor

try:
  from collections import OrderedDict
except ImportError:
  from ordereddict import OrderedDict


class Datastore(object):
  '''A Datastore represents storage for any key-value pair.

  Datastores are general enough to be backed by all kinds of different storage:
  in-memory caches, databases, a remote datastore, flat files on disk, etc.

  The general idea is to wrap a more complicated storage facility in a simple,
  uniform interface, keeping the freedom of using the right tools for the job.
  In particular, a Datastore can aggregate other datastores in interesting ways,
  like sharded (to distribute load) or tiered access (caches before databases).

  While Datastores should be written general enough to accept all sorts of
  values, some implementations will undoubtedly have to be specific (e.g. SQL
  databases where fields should be decomposed into columns), particularly to
  support queries efficiently.

  '''

  # Main API. Datastore mplementations MUST implement these methods.

  def get(self, key):
    '''Return the object named by key or None if it does not exist.
    None takes the role of default value, so no KeyError exception is raised.

    Args:
      key: Key naming the object to retrieve

    Returns:
      object or None
    '''
    raise NotImplementedError

  def put(self, key, value):
    '''Stores the object `value` named by `key`.
    How to serialize and store objects is up to the underlying datastore.
    It is recommended to use simple objects (strings, numbers, lists, dicts).

    Args:
      key: Key naming `value`
      value: the object to store.
    '''
    raise NotImplementedError

  def delete(self, key):
    '''Removes the object named by `key`.

    Args:
      key: Key naming the object to remove.
    '''
    raise NotImplementedError

  def query(self, query):
    '''Returns an iterable of objects matching criteria expressed in `query`
    Implementations of query will be the largest differentiating factor
    amongst datastores. All datastores **must** implement query, even using
    query's worst case scenario, see Query class for details.

    Args:
      query: Query object describing the objects to return.

    Raturns:
      Cursor with all objects matching criteria
    '''
    raise NotImplementedError

  # Secondary API. Datastores MAY provide optimized implementations.

  def contains(self, key):
    '''Returns whether the object named by `key` exists.
    The default implementation pays the cost of a get. Some datastore
    implementations may optimize this.

    Args:
      key: Key naming the object to check.

    Returns:
      boalean whether the object exists
    '''
    return self.get(key) is not None




class DictDatastore(Datastore):
  '''Simple straw-man in-memory datastore backed by a dict.'''

  def __init__(self):
    self._items = OrderedDict()

  def _collection(self, key):
    collection = str(key.path)
    if not collection in self._items:
      self._items[collection] = OrderedDict()
    return self._items[collection]

  def get(self, key):
    '''Return the object named by key.'''
    try:
      return self._collection(key)[key]
    except KeyError, e:
      return None

  def put(self, key, value):
    '''Stores the object.'''
    if value is None:
      self.delete(key)
    else:
      self._collection(key)[key] = value

  def delete(self, key):
    '''Removes the object.'''
    try:
      del self._collection(key)[key]

      if len(self._collection(key)) == 0:
        del self._items[str(key.path)]
    except KeyError, e:
      pass

  def contains(self, key):
    '''Returns whether the object is in this datastore.'''
    return key in self._collection(key)

  def query(self, query):
    '''Returns a sequence of objects matching criteria expressed in `query`'''
    # entire dataset already in memory, so ok to apply query naively
    if str(query.key) in self._items:
      return query(self._items[str(query.key)].values())
    else:
      return query([])

  def __len__(self):
    return sum(map(len, self._items.values()))




class InterfaceMappingDatastore(Datastore):
  '''Represents simple wrapper datastore around an object that, though not a
  Datastore, implements data storage through a similar interface. For example,
  memcached and redis both implement a `get`, `set`, `delete` interface.
  '''

  def __init__(self, service, get='get', put='put', delete='delete', key=str):
    '''Initialize the datastore with given `service`.

    Args:
      service: A service that provides data storage through a similar interface
          to Datastore. Using the service should only require a simple mapping
          of methods, such as {put : set}.

      get:    The attribute name of the `service` method implementing get
      put:    The attribute name of the `service` method implementing put
      delete: The attribute name of the `service` method implementing delete

      key: A function converting a Datastore key (of type Key) into a `service`
          key. The conversion will often be as simple as `str`.
    '''
    self._service = service
    self._service_key = key

    self._service_ops = {}
    self._service_ops['get'] = getattr(service, get)
    self._service_ops['put'] = getattr(service, put)
    self._service_ops['delete'] = getattr(service, delete)
    # AttributeError will be raised if service does not implement the interface


  def get(self, key):
    '''Return the object in `service` named by `key` or None.

    Args:
      key: Key naming the object to retrieve.

    Returns:
      object or None
    '''
    key = self._service_key(key)
    return self._service_ops['get'](key)

  def put(self, key, value):
    '''Stores the object `value` named by `key` in `service`.

    Args:
      key: Key naming `value`.
      value: the object to store.
    '''
    key = self._service_key(key)
    self._service_ops['put'](key, value)

  def delete(self, key):
    '''Removes the object named by `key` in `service`.

    Args:
      key: Key naming the object to remove.
    '''
    key = self._service_key(key)
    self._service_ops['delete'](key)






class ShimDatastore(Datastore):
  '''Represents a non-concrete datastore that adds functionality between the
  client and a lower level datastore. Shim datastores do not actually store
  data themselves; instead, they delegate storage to an underlying child
  datastore. The default implementation just passes all calls to the child.
  '''

  def __init__(self, datastore):
    '''Initializes this ShimDatastore with child `datastore`.'''

    if not isinstance(datastore, Datastore):
      errstr = 'datastore must be of type %s. Got %s.'
      raise TypeError(errstr % (Datastore, datastore))

    self.child_datastore = datastore

  # default implementation just passes all calls to child
  def get(self, key):
    return self.child_datastore.get(key)

  def put(self, key, value):
    self.child_datastore.put(key, value)

  def delete(self, key):
    self.child_datastore.delete(key)

  def query(self, query):
    return self.child_datastore.query(query)


class DatastoreCollection(ShimDatastore):
  '''Represents a collection of datastores.'''

  def __init__(self, stores=[]):
    '''Initialize the datastore with any provided datastores.'''
    if not isinstance(stores, list):
      stores = list(stores)

    for store in stores:
      if not isinstance(store, Datastore):
        raise TypeError("all stores must be of type %s" % Datastore)

    self._stores = stores

  def datastore(self, index):
    return self._stores[index]

  def appendDatastore(self, store):
    if not isinstance(store, Datastore):
      raise TypeError("stores must be of type %s" % Datastore)

    self._stores.append(store)

  def removeDatastore(self, store):
    self._stores.remove(store)

  def insertDatastore(self, index, store):
    if not isinstance(store, Datastore):
      raise TypeError("stores must be of type %s" % Datastore)

    self._stores.insert(index, store)





class TieredDatastore(DatastoreCollection):
  '''Represents a hierarchical collection of datastores.

  Each datastore is queried in order. This is helpful to organize access
  order in terms of speed (i.e. read caches first).

  Datastores should be arranged in order of completeness, with the most complete
  datastore last.

  Semantics:
    get      : returns first found value
    put      : writes through to all
    delete   : deletes through to all
    contains : returns first found value
    query    : queries bottom (most complete) datastore

  '''

  def get(self, key):
    '''Return the object named by key.'''
    value = None
    for store in self._stores:
      value = store.get(key)
      if value is not None:
        break

    # add model to lower stores only
    if value is not None:
      for store2 in self._stores:
        if store == store2:
          break
        store2.put(key, value)

    return value

  def put(self, key, value):
    '''Stores the object in all stores.'''
    for store in self._stores:
      store.put(key, value)

  def delete(self, key):
    '''Removes the object from all stores.'''
    for store in self._stores:
      store.delete(key)

  def contains(self, key):
    '''Returns whether the object is in this datastore.'''
    for store in self._stores:
      if store.contains(key):
        return True
    return False

  def query(self, query):
    '''Returns a sequence of objects matching criteria expressed in `query`'''
    # queries hit the last (most complete) datastore
    return self._stores[-1].query(query)





class ShardedDatastore(DatastoreCollection):
  '''Represents a collection of datastore shards.
  A datastore is selected based on a sharding function.

  sharding functions should take a Key and return an integer.

  WARNING: adding or removing datastores while mid-use may severely affect
           consistency. Also ensure the order is correct upon initialization.
           While this is not as important for caches, it is crucial for
           persistent datastores.

  '''

  def __init__(self, stores=[], shardingfn=hash):
    '''Initialize the datastore with any provided datastore.'''
    if not callable(shardingfn):
      raise TypeError('shardingfn (type %s) is not callable' % type(shardingfn))

    super(ShardedDatastore, self).__init__(stores)
    self._shardingfn = shardingfn


  def shard(self, key):
    return self._shardingfn(key) % len(self._stores)

  def shardDatastore(self, key):
    return self.datastore(self.shard(key))


  def get(self, key):
    '''Return the object named by key from the corresponding datastore.'''
    return self.shardDatastore(key).get(key)

  def put(self, key, value):
    '''Stores the object to the corresponding datastore.'''
    self.shardDatastore(key).put(key, value)

  def delete(self, key):
    '''Removes the object from the corresponding datastore.'''
    self.shardDatastore(key).delete(key)

  def contains(self, key):
    '''Returns whether the object is in this datastore.'''
    return self.shardDatastore(key).contains(key)

  def query(self, query):
    '''Returns a sequence of objects matching criteria expressed in `query`'''
    cursor = Cursor(query, self.shard_query_generator(query))
    cursor.apply_order()  # ordering sharded queries is expensive (no generator)
    return cursor

  def shard_query_generator(self, query):
    '''A generator that queries each shard in sequence.'''
    shard_query = query.copy()

    for shard in self._stores:
      # yield all items matching within this shard
      cursor = shard.query(shard_query)
      for item in cursor:
        yield item

      # update query with results of first query
      shard_query.offset = max(shard_query.offset - cursor.skipped, 0)
      if shard_query.limit:
        shard_query.limit = max(shard_query.limit - cursor.returned, 0)

        if shard_query.limit <= 0:
          break  # we're already done!
