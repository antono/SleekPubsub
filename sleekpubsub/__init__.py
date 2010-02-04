import sleekxmpp.componentxmpp
from sleekxmpp.xmlstream.matcher.xmlmask import MatchXMLMask
from sleekxmpp.xmlstream.matcher.stanzapath import StanzaPath
from sleekxmpp.xmlstream.handler.callback import Callback
from sleekxmpp.plugins import stanza_pubsub as Pubsub
from sleekxmpp.exceptions import XMPPError
from xml.etree import cElementTree as ET
import uuid
from . db import PubsubDB
from . node import BaseNode
import logging
from . adhoc import PubsubAdhoc
from . httpd import HTTPD

class PublishSubscribe(object):
	
	def __init__(self, xmpp, dbfile, config):
		self.xmpp = xmpp
		self.dbfile = dbfile
		self.nodeplugins = []
		
		self.config = config
		self.default_config = self.getDefaultConfig()
		self.nodeset = set()
		
		self.admins = []
		self.node_classes = {'leaf': BaseNode}
		self.nodes = {}
		self.adhoc = PubsubAdhoc(self)
		self.http = HTTPD(self)

		self.xmpp.registerHandler(Callback('pubsub publish', MatchXMLMask("<iq xmlns='%s' type='set'><pubsub xmlns='http://jabber.org/protocol/pubsub'><publish xmlns='http://jabber.org/protocol/pubsub' /></pubsub></iq>" % self.xmpp.default_ns), self.handlePublish)) 
		#self.xmpp.registerHandler(Callback('pubsub create', MatchXMLMask("<iq xmlns='%s' type='set'><pubsub xmlns='http://jabber.org/protocol/pubsub'><create xmlns='http://jabber.org/protocol/pubsub' /></pubsub></iq>" % self.xmpp.default_ns), self.handleCreateNode)) 
		self.xmpp.registerHandler(Callback('pubsub create', StanzaPath("iq@type=set/pubsub/publish"), self.handleCreateNode)) 
		self.xmpp.registerHandler(Callback('pubsub configure', MatchXMLMask("<iq xmlns='%s' type='set'><pubsub xmlns='http://jabber.org/protocol/pubsub#owner'><configure xmlns='http://jabber.org/protocol/pubsub#owner' /></pubsub></iq>" % self.xmpp.default_ns), self.handleConfigureNode)) 
		self.xmpp.registerHandler(Callback('pubsub get configure', MatchXMLMask("<iq xmlns='%s' type='get'><pubsub xmlns='http://jabber.org/protocol/pubsub#owner'><configure xmlns='http://jabber.org/protocol/pubsub#owner' /></pubsub></iq>" % self.xmpp.default_ns), self.handleGetNodeConfig)) 
		self.xmpp.registerHandler(Callback('pubsub defaultconfig', MatchXMLMask("<iq xmlns='%s' type='get'><pubsub xmlns='http://jabber.org/protocol/pubsub#owner'><default xmlns='http://jabber.org/protocol/pubsub#owner' /></pubsub></iq>" % self.xmpp.default_ns), self.handleGetDefaultConfig)) 
		self.xmpp.registerHandler(Callback('pubsub subscribe', MatchXMLMask("<iq xmlns='%s' type='set'><pubsub xmlns='http://jabber.org/protocol/pubsub'><subscribe xmlns='http://jabber.org/protocol/pubsub' /></pubsub></iq>" % self.xmpp.default_ns), self.handleSubscribe)) 
		self.xmpp.registerHandler(Callback('pubsub unsubscribe', MatchXMLMask("<iq xmlns='%s' type='set'><pubsub xmlns='http://jabber.org/protocol/pubsub'><unsubscribe xmlns='http://jabber.org/protocol/pubsub' /></pubsub></iq>" % self.xmpp.default_ns), self.handleUnsubscribe)) 
		self.xmpp.add_event_handler("session_start", self.start)
		self.xmpp.add_event_handler("changed_subscription", self.handlePresenceSubscribe)
		self.xmpp.add_event_handler("got_online", self.handleGotOnline)
	
	def start(self, event):
		self.db = PubsubDB(self.dbfile, self.xmpp)
		self.loadNodes()
		for jid, pfrom in self.db.getRoster():
			if not pfrom: pfrom = self.xmpp.jid
			self.xmpp.sendPresence(pto=jid, ptype='probe', pfrom=pfrom)
			self.xmpp.sendPresence(pto=jid, pfrom=pfrom)

	def handleGotOnline(self, pres):
		pfrom = pres['to'].user
		if pfrom: pfrom += "@"
		pfrom += self.xmpp.jid
		self.xmpp.sendPresence(pto=pres['from'].bare, pfrom=pfrom)

	def handlePresenceSubscribe(self, pres):
		ifrom = pres['from'].bare
		ito = pres['to'].bare
		subto, subfrom = self.db.getRosterJid(ifrom)
		if True: # pres['to'] == self.xmpp.jid:
			if pres['type'] == 'subscribe':
				if not subto:
					self.xmpp.sendPresenceSubscription(pto=ifrom, pfrom=ito, ptype='subscribed')
					self.db.setRosterTo(ifrom, True, ito)
				if not subfrom:
					self.xmpp.sendPresenceSubscription(pto=ifrom, pfrom=ito, ptype='subscribe')
				self.xmpp.sendPresence(pto=ifrom)
			elif pres['type'] == 'unsubscribe':
				self.xmpp.sendPresenceSubscription(pto=ifrom,  pfrom=ito, ptype='unsubscribed')
				self.xmpp.sendPresenceSubscription(pto=ifrom,  pfrom=ito, ptype='unsubscribe')
				self.db.clearRoster(ifrom)
			elif pres['type'] == 'subscribed':
				if not subfrom:
					self.db.setRosterFrom(ifrom, True)
				if not subto:
					self.xmpp.sendPresenceSubscription(pto=ifrom, pfrom=ito, ptype='subscribed')
					self.db.setRosterTo(ifrom, True, ito)

	def getDefaultConfig(self):
		default_config = self.xmpp.plugin['xep_0004'].makeForm()
		default_config.addField('FORM_TYPE', 'hidden', value='http://jabber.org/protocol/pubsub#node_config')
		ntype = default_config.addField('pubsub#node_type', 'list-single', label='Select the node type', value='leaf')
		ntype.addOption('leaf', 'Leaf')
		default_config.addField('pubsub#title', label='A friendly name for the node')
		default_config.addField('pubsub#deliver_notifications', 'boolean', label='Deliver event notifications', value=True)
		default_config.addField('pubsub#deliver_payloads', 'boolean', label='Deliver payloads with event notifications', value=True)
		default_config.addField('pubsub#notify_config', 'boolean', label='Notify subscribers when the node configuration changes', value=False)
		default_config.addField('pubsub#notify_delete', 'boolean', label='Notify subscribers when the node is deleted', value=False)
		default_config.addField('pubsub#notify_retract', 'boolean', label='Notify subscribers when items are removed from the node', value=False)
		default_config.addField('pubsub#notify_sub', 'boolean', label='Notify owners about new subscribers and unsubscribes', value=False)
		default_config.addField('pubsub#persist_items', 'boolean', label='Persist items in storage', value=False)
		default_config.addField('pubsub#max_items', label='Max # of items to persist', value='10')
		default_config.addField('pubsub#subscribe', 'boolean', label='Whether to allow subscriptions', value=True)
		model = default_config.addField('pubsub#access_model', 'list-single', label='Specify the subscriber model', value='open')
		model.addOption('authorize', 'Authorize')
		model.addOption('open', 'Open')
		model.addOption('whitelist', 'whitelist')
		model = default_config.addField('pubsub#publish_model', 'list-single', label='Specify the publisher model', value='publishers')
		model.addOption('publishers', 'Publishers')
		model.addOption('subscribers', 'Subscribers')
		model.addOption('open', 'Open')
		model = default_config.addField('pubsub#send_last_published_item', 'list-single', label='Send last published item', value='never')
		model.addOption('never', 'Never')
		model.addOption('on_sub', 'On Subscription')
		model.addOption('on_sun_and_presence', 'On Subscription And Presence')
		default_config.addField('pubsub#presence_based_delivery', 'boolean', label='Deliver notification only to available users', value=False)
		return default_config
	
	def loadNodes(self):
		for node, node_type in self.db.getNodes():
			self.nodes[node] = self.node_classes.get(node_type, BaseNode)(self, self.db, node)
			self.nodeset.update((node,))

	def registerNodeType(self, nodemodule):
		self.nodeplugins.append(nodemodule.extension_class(self))
	
	def registerNodeClass(self, nodeclass):
		self.node_classes[nodeclass.nodetype] = nodeclass
		self.default_config.field['pubsub#node_type'].addOption(nodeclass.nodetype, nodeclass.nodetype.title())
	
	def deleteNode(self, node):
		if node in self.nodes:
			del self.nodes[node]
			self.nodeset.discard(node)
			
			self.db.deleteNode(node)
			return True
		else:
			return False
	
	def modifyAffiliations(node, updates={}):
		if node in self.nodes:
			return self.nodes[node].modifyAffiliations(updates)
		else:
			return False
	
	def handlePublish(self, stanza):
		"""iq/{http://jabber.org/protocol/pubsub}pubsub/{http://jabber.org/protocol/pubsub}publish"""
		node = self.nodes.get(stanza['pubsub']['publish']['node'])
		ids = []
		if node is None:
			raise XMPPError('item-not-found')
		for item in stanza['pubsub']['publish']:
			item_id = self.publish(stanza['pubsub']['publish']['node'], item['payload'], item['id'], stanza['from'].bare)
			ids.append(item_id)
		stanza.reply()
		stanza['pubsub'].clear()
		for id in ids:
			stanza.append(Pubsub.Item({'id': id}))
		stanza.send()
	
	def publish(self, node, item, id=None, who=None):
		if isinstance(node, str):
			node = self.nodes.get(node)
		return node.publish(item, id, who=who)
	
	def handleGetDefaultConfig(self, stanza):
		stanza.reply()
		stanza['pubsub']['default']['config'] = self.default_config
		stanza.send()
	
	def createNode(self, node, config=None, who=None):
		if config is None:
			config = self.default_config.copy()
		else:
			config = self.default_config.merge(config)
		config = config.getValues()
		nodeclass = self.node_classes.get(config['pubsub#node_type'])
		if node in self.nodeset or nodeclass is None:
			return False
		if who:
			who = self.xmpp.getjidbare(who)
		self.nodes[node] = nodeclass(self, self.db, node, config, owner=who, fresh=True)
		self.nodeset.update((node,))
		return True
	
	def handleCreateNode(self, iq):
		node = iq['pubsub']['create']['node'] or uuid.uuid4().hex
		config = iq['pubsub']['create']['configure']['config'] or self.default_config
		if node in self.nodes:
			raise XMPPError('conflict', etype='cancel')
		if not self.createNode(node, config. iq['from']):
			raise XMPPError()
		iq.reply()
		iq['pubsub'].clear()
		iq['pubsub']['create']['node'] = node
		iq.send()
	
	def configureNode(self, node, config):
		if node not in self.nodeset:
			return False
		config = self.default_config.merge(config).getValues()
		self.nodes[node].configure(config)
		return True
	
	def handleConfigureNode(self, stanza):
		xml = stanza.xml
		configure = xml.find('{http://jabber.org/protocol/pubsub#owner}pubsub/{http://jabber.org/protocol/pubsub#owner}configure')
		node = configure.get('node')
		xform = xml.find('{http://jabber.org/protocol/pubsub#owner}pubsub/{http://jabber.org/protocol/pubsub#owner}configure/{jabber:x:data}x')
		if xform is None or not self.configureNode(node, self.xmpp.plugin['xep_0004'].buildForm(xform)):
			self.xmpp.send(self.xmpp.makeIqError(xml.get('id')))
			return
		iq = self.xmpp.makeIqResult(xml.get('id'))
		iq.attrib['from'] = self.xmpp.jid
		iq.attrib['to'] = xml.get('from')
		self.xmpp.send(iq)
	
	def subscribeNode(self, node, jid, who=None, to=None):
		if node not in self.nodeset:
			return False
		return self.nodes[node].subscribe(jid, who, to=to)
	
	def handleSubscribe(self, stanza):
		node = stanza['pubsub']['subscribe']['node']
		jid = stanza['pubsub']['subscribe']['jid'].full
		subid = self.subscribeNode(node, jid, stanza['from'].bare)
		if not subid:
			self.xmpp.send(self.xmpp.makeIqError(xml.get('id')))
			return
		stanza.reply()
		stanza.clear()
		stanza['pubsub']['subscription']['subid'] = subid
		stanza['pubsub']['subscription']['node'] = node
		stanza['pubsub']['subscription']['jid'] = jid
		stanza['pubsub']['subscription']['subscription'] = 'subscribed'
		stanza.send()
	
	def handleUnsubscribe(self, stanza):
		xml = stanza.xml
		subscribe = xml.find('{http://jabber.org/protocol/pubsub}pubsub/{http://jabber.org/protocol/pubsub}unsubscribe')
		node = subscribe.get('node')
		jid = subscribe.get('jid')
		subid = subscribe.get('subid')
		if node not in self.nodeset:
			self.xmpp.send(self.xmpp.makeIqError(xml.get('id')))
			return
		self.nodes[node].unsubscribe(jid, xml.get('from'), subid)
		iq = self.xmpp.makeIqResult(xml.get('id'))
		iq.attrib['from'] = self.xmpp.jid
		iq.attrib['to'] = xml.get('from')
		self.xmpp.send(iq)
	
	def unsubscribeNode(node, jid, who=None, subid=None):
		if node in self.nodes:
			self.nodes[node].unsubscribe(jid, who, subid)
			return True
		else:
			return False
	
	def getNodeConfig(self, node):
		if node not in self.nodeset:
			return False
		config = self.default_config.copy()
		config.setValues(self.nodes[node].getConfig())
		return config
	
	def handleGetNodeConfig(self, stanza):
		xml = stanza.xml
		configure = xml.find('{http://jabber.org/protocol/pubsub#owner}pubsub/{http://jabber.org/protocol/pubsub#owner}configure')
		node = configure.get('node')
		config = self.getNodeConfig(node)
		if config == False:
			self.xmpp.send(self.xmpp.makeIqError(xml.get('id')))
			return
		config = config.getXML('form')
		iq = self.xmpp.makeIqResult(xml.get('id'))
		iq.attrib['from'] = self.xmpp.jid
		iq.attrib['to'] = xml.get('from')
		pubsub = ET.Element('{http://jabber.org/protocol/pubsub#owner}pubsub')
		configure = ET.Element('{http://jabber.org/protocol/pubsub#owner}configure', {'node': node})
		configure.append(config)
		pubsub.append(configure)
		iq.append(pubsub)
		self.xmpp.send(iq)
