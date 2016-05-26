# -*- coding: utf-8 -*-

from __future__ import absolute_import

import antlr4
from antlr4.error.ErrorListener import ErrorListener

from .ADQLLexer import ADQLLexer
from .ADQLParser import ADQLParser
from .ADQLParserVisitor import ADQLParserVisitor
from .ADQLParserListener import ADQLParserListener


def _remove_children(ctx):
        for i in range(ctx.getChildCount() - 1):
            ctx.removeLastChild()


class SyntaxErrorListener(ErrorListener):
    def __init__(self):
        super(SyntaxErrorListener, self).__init__()
        self.syntax_errors = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):
        self.syntax_errors.append((line, column, offendingSymbol.text))


class ADQLtoMySQLGeometryTranslationVisitor(ADQLParserVisitor):
    """
    This thing has a shitty hack in it but that's the only way I could get
    it to work. It's like this:

    1) Find the rule we need to translate. Point, for example.
    2) After processing it, get rid of all off its children except the first
       one. This still keeps a token in the stream so it can be accessed later
       by other visitors or listeners. Otherwise is impossible (or hard?) to
       stick a new token in the stream so the walking works flawlessly.
    3) Hash the replacement string in the contexts dictionary. Use the context
       as a hash. This way we can return the hashed string instead the
       token so we effectively translate the rule.

    :param conunits:
        What should we be converting the units to. If no conversion is
        necessary, just pass an empty string.

    """
    def __init__(self, conunits="RADIANS"):
        self.contexts = {}
        self.limit = None
        self.conunits = conunits

    def _convert_values(self, ctx, cidx):
        """
        Values inside the ADQL functions can be floats, expressions, or
        strings. Strings need to be treated differently because
        mysql-sphere syntax differs slightly if we pass a column name
        instead of a value.

        :param ctx:
            antlr context.

        :param cidx:
            Context child index.

        """
        vals = []
        for i in ctx.children[cidx].getText().split(','):
            try:
                v = float(i)
            except ValueError:
                try:
                    v = float(eval(i))
                except (AttributeError, ValueError):
                    v = i.replace('"', '`')

            vals.append(v)

        return vals

    def visitRegular_identifier(self, ctx):
        self.contexts[ctx] = ctx.getText().replace("'", "`").replace('"', "`")

    def visitSet_limit(self, ctx):
        """
        TOP N needs to go to the back of the query and it needs to become
        LIMIT N.

        :param ctx:
            antlr context.

        """
        self.limit = int(ctx.children[1].getText())
        ctx.removeLastChild()
        ctx.removeLastChild()

    def visitPoint(self, ctx):
        coords = []
        for j in (2, 4):
            coords.extend(self._convert_values(ctx, j))
        if len(coords) == 3:
            coords = coords[1:]

        ctx_text = "spoint( %s(%s),%s(%s) )" % (self.conunits, coords[0],
                                                self.conunits, coords[1])

        _remove_children(ctx)
        self.contexts[ctx] = ctx_text

    def visitBox(self, ctx):
        s = 4
        pars = []
        for j in range(0, 5, 2):
            pars.extend(self._convert_values(ctx, s + j))

        ctx_text = "sbox( spoint(%s(%s),%s(%s)),spoint(%s(%s),%s(%s)) )" %\
            (self.conunits, pars[0], self.conunits, pars[1],
             self.conunits, pars[2], self.conunits, pars[3])

        _remove_children(ctx)
        self.contexts[ctx] = ctx_text

    def visitCircle(self, ctx):
        s = 4
        pars = []
        for j in range(0, 3, 2):
            pars.extend(self._convert_values(ctx, s + j))

        ctx_text = "scircle( spoint(%s(%s), %s(%s)), %s(%s) )" %\
            (self.conunits, pars[0], self.conunits, pars[1],
             self.conunits, pars[2])

        _remove_children(ctx)
        self.contexts[ctx] = ctx_text

    def visitPolygon(self, ctx):
        raise AttributeError("Polygons are not supported (yet).")
        #  cc = ctx.getChildCount()
        #  s = 4
        #  pars = []
        #  for j in range(0, cc - 5, 2):
        #  pars.extend(self._convert_values(ctx, s + j))
        #  pars = self._wrap_strings(pars)

        #  poly = "spoly( '{"
        #  for i in range(0, len(wunits), 4):
        #  poly += '(%s%s,%s%s),' % wunits[i:i + 4]
        #  ctx_text = poly[:-1] + "}' )"

        #  _remove_children(ctx)
        #  self.contexts[ctx] = ctx_text


class ADQLtoMySQLFunctionsTranslationVisitor(ADQLParserVisitor):
    """
    Run this visitor after the geometry has already been processed.

    :param context:
        A dictionary that was created in the previous run and includes
        the replaced geometry chunks.

    """
    def __init__(self, contexts):
        self.contexts = contexts

    def visitArea(self, ctx):
        arg = self.contexts[ctx.children[2].children[0].children[0]]
        ctx_text = 'sarea(%s)' % arg
        for i in range(ctx.getChildCount() - 1):
            ctx.removeLastChild()
        self.contexts[ctx] = ctx_text

    def visitCentroid(self, ctx):
        """
        Works only for circles.

        """
        arg = self.contexts[ctx.children[2].children[0].children[0]]
        ctx_text = 'scenter(%s)' % arg
        _remove_children(ctx)
        self.contexts[ctx] = ctx_text

    def visitContains(self, ctx):
        arg = (self.contexts[ctx.children[2].children[0].children[0]],
               self.contexts[ctx.children[4].children[0].children[0]])
        ctx_text = 'srcontainsl(%s, %s)' % arg
        _remove_children(ctx)
        self.contexts[ctx] = ctx_text

    def visitDistance(self, ctx):
        arg = (self.contexts[ctx.children[2].children[0]],
               self.contexts[ctx.children[4].children[0]])
        ctx_text = 'sdist(%s, %s)' % arg
        for i in range(ctx.getChildCount() - 1):
            ctx.removeLastChild()
        self.contexts[ctx] = ctx_text

    def visitIntersects(self, ctx):
        arg = (self.contexts[ctx.children[2].children[0].children[0]],
               self.contexts[ctx.children[4].children[0].children[0]])
        ctx_text = 'soverlaps(%s, %s)' % arg
        _remove_children(ctx)
        self.contexts[ctx] = ctx_text


class FormatListener(ADQLParserListener):
    """
    Used for formating the output query.

    """
    def __init__(self, parser, contexts, limit):
        self._parser = parser
        self.nodes = []
        self.contexts = contexts
        self.limit = limit

    def visitTerminal(self, node):
        try:
            self.nodes.append(self.contexts[node.parentCtx])
        except KeyError:
            self.nodes.append(node.getText())

    def format_query(self):
        query = ' '.join(self.nodes)
        if self.limit:
            query += ' LIMIT %d' % self.limit
        return query


class ADQLQueryTranslator(object):
    """
    The main translator object used to do the actual translation.

    :param query:
        ADQL query string.

    """
    def __init__(self, query=None):
        self.syntax_error_listener = SyntaxErrorListener()
        self.parsed = False
        self._query = None

        if query is not None:
            self.set_query(query)

    def parse(self):
        """
        Parse the input query and store the output in self.tree.

        """
        inpt = antlr4.InputStream(self.query)
        lexer = ADQLLexer(inpt)
        self.stream = antlr4.CommonTokenStream(lexer)
        self.parser = ADQLParser(self.stream)
        self.parser._listeners = [self.syntax_error_listener]

        self.tree = self.parser.query_expression()
        self.parsed = True

        if len(self.syntax_error_listener.syntax_errors):
            print (self.syntax_error_listener.syntax_errors)
            raise RuntimeError("ADQL query has errors.")
        self.walker = antlr4.ParseTreeWalker()

    @property
    def query(self):
        """
        Get the query string.

        """
        return self._query

    def set_query(self, query):
        """
        Set the query string.

        :param value:
            Query string.

        """
        self._query = query
        self.parse()

    def to_mysql(self):
        """
        Translate ADQL query to a MySQL query using mysql_sphere plugin
        for the spherical functions.

        """
        assert self.parsed, 'No query given or query not parsed yet.'
        translator_visitor = ADQLtoMySQLGeometryTranslationVisitor()
        translator_visitor.visit(self.tree)
        limit = translator_visitor.limit
        translator_visitor = \
            ADQLtoMySQLFunctionsTranslationVisitor(translator_visitor.contexts)
        translator_visitor.visit(self.tree)
        self.format_listener = FormatListener(self.parser,
                                              translator_visitor.contexts,
                                              limit)
        self.walker.walk(self.format_listener, self.tree)
        return self.format_listener.format_query()
