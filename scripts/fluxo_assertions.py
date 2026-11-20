"""Verificações independentes da fonte real e da versão apresentada no chat."""

import re,unicodedata,hashlib,html

def fold(s):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(s)).lower() if not unicodedata.combining(c))

def compact(s):
    value=str(s)
    for _ in range(3):value=html.unescape(value)
    return ' '.join(value.split())

def source_parts(page):
    text=page['content'].split('\n## Texto da página',1)[0]
    ingredients_start=re.search(r'^## Ingredientes',text,re.M)
    headers=[m for m in re.finditer(r'^# [^\n]+$',text,re.M) if ingredients_start and m.start()<ingredients_start.start()]
    if headers:text=text[headers[-1].start():]
    text=text.split('\n# ',1)[0]
    ingredients=re.search(r'^## Ingredientes[^\n]*\n(.+?)(?=\n## |\Z)',text,re.M|re.S)
    method=re.search(r'^## Modo de preparo[^\n]*\n(.+?)(?=\n## |\Z)',text,re.M|re.S)
    assert ingredients and method,'snapshot web não contém ingredientes e preparo completos'
    lines=[compact(line[2:]) for line in ingredients[1].splitlines() if line.startswith('- ')]
    return lines,re.sub(r'^\d+\.\s*','',method[1].strip(),flags=re.M)

def assert_contract(recipe,pages,rendered):
    dados=recipe['dados'];contract=dados.get('fonte_verificada')
    assert isinstance(contract,dict),'a ficha web não guardou o contrato fonte_verificada'
    assert contract.get('url')==recipe.get('fonte_url'),'a URL da ficha difere da URL da fonte verificada'
    snapshots=[p for p in pages if p.get('url')==contract.get('url')]
    assert snapshots,'a fonte atribuída à ficha não consta dos resultados reais de web_extract'
    lines,method=source_parts(snapshots[-1])
    assert [compact(i) for i in contract.get('ingredientes',[])]==lines,'o contrato fonte_verificada perdeu ingredientes do snapshot'
    saved_method=contract.get('modo_preparo')
    if isinstance(saved_method,list):saved_method='\n'.join(saved_method)
    saved_method=re.sub(r'^\d+\.\s*','',str(saved_method),flags=re.M)
    assert compact(saved_method)==compact(method),'o contrato fonte_verificada perdeu passos do preparo'
    assert contract.get('nome'), 'fonte sem identificação'
    assert dados.get('tempo_preparo_min') and dados['tempo_preparo_min']<=120,'duração atual não foi validada para o perfil de teste'
    actual_names=' '.join(fold(i.get('nome','')) for i in dados.get('ingredientes',[]) if isinstance(i,dict))
    fidelity=dados.get('fidelidade_fonte') or {}
    differences=fidelity.get('diferencas') or []
    adaptation=str(dados.get('adaptacao') or fidelity.get('adaptacao') or '').strip()
    if not contract.get('tempo_preparo_min'):
        # Uma adaptação tem duração própria, explicitamente estimada. Não é
        # correto inventar um tempo publicado que a página não informou.
        assert adaptation, 'receita original sem duração publicada precisa de confirmação'
        assert re.search(r'cerca de|estimad|aproxim',fold(rendered)), 'tempo próprio da adaptação não foi apresentado como estimativa'
    if differences:
        assert adaptation,'ficha divergente não foi identificada como adaptação'
        assert 'adapt' in fold(rendered),'a resposta não explicou que a versão é adaptada'
        rendered_clean=fold(compact(rendered))
        for difference in differences:
            assert fold(compact(difference)) in rendered_clean, 'mudança não foi apresentada antes do aceite: '+str(difference)
    if contract.get('porcoes') is not None and recipe.get('porcoes')!=contract.get('porcoes'):
        assert adaptation and any('rendimento' in fold(d) or 'porc' in fold(d) for d in differences),'rendimento alterado sem apresentação da mudança'
    elif contract.get('porcoes') is None:
        assert adaptation and re.search(r'rendimento.{0,30}\d+.{0,5}porc',fold(rendered)), 'rendimento próprio não foi declarado na adaptação'
    if '15354-frango-assado-com-batatas' in contract['url']:
        # Referência independente: a página real escolhida tem estes sete itens e rende cinco.
        assert len(lines)==7 and contract['porcoes']==5,'o snapshot da fonte conhecida divergiu: revisar antes de aprovar o teste'
        groups=[('frango',('frango',)),('batata',('batata',)),('molho de tomate',('molho de tomate','pomarola','extrato de tomate')),
                ('maionese',('maionese',)),('tempero de aves',('sazon','tempero para aves','tempero de aves')),('alho',('alho',)),('sal',('sal',))]
        for name,aliases in groups:
            covered=any(re.search(r'\b'+re.escape(alias)+r'\b',actual_names) for alias in aliases)
            if not covered:
                assert adaptation,'a ficha omitiu ingrediente da fonte sem adaptação: '+name
                assert any(any(alias in fold(d) for alias in aliases) for d in differences),'omissão não declarada nas diferenças: '+name
        if not adaptation:
            chicken=next(i for i in dados['ingredientes'] if 'frango' in fold(i.get('nome','')))
            amount=float(chicken['quantidade'])/(1000 if fold(chicken['unidade'])=='g' else 1)
            assert abs(amount-1.5)<1e-8,'quantidade de frango alterada sem adaptação'

    if fidelity.get('faltantes'):
        adapted_method=str(dados.get('modo_preparo') or '').strip()
        assert adapted_method,'a versão alterada não guardou seu próprio modo de preparo'
        clean_adapted=re.sub(r'^\d+\.\s*','',adapted_method,flags=re.M)
        assert compact(clean_adapted)!=compact(method),'a adaptação retirou ingredientes e manteve o preparo original contraditório'
        assert fold(compact(adapted_method)) in fold(compact(rendered)),'o preparo adaptado não foi mostrado antes do aceite'
    requirements=' '.join(str(i) for i in dados.get('equipamentos_necessarios') or [])
    skills=' '.join(str(i) for i in dados.get('tecnicas_necessarias') or [])
    if 'forno' in fold(method):
        assert 'forno' in fold(requirements),'receita de forno aprovada sem conferir forno'
        assert any(t in fold(skills) for t in ('assar','assado')),'receita de forno aprovada sem conferir habilidade de assar'
    if any(t in fold(method) for t in ('assadeira','refratario','forma')):
        assert any(t in fold(requirements) for t in ('assadeira','refratario','forma')),'receita em assadeira aprovada sem conferir recipiente'
    return {'fonte':contract['url'],'ingredientes_na_fonte':len(lines),'preparo_sha256':hashlib.sha256(compact(method).encode()).hexdigest(),
            'versao_adaptada':bool(adaptation),'diferencas_apresentadas':len(differences),'equipamentos_conferidos':dados.get('equipamentos_necessarios'),'tecnicas_conferidas':dados.get('tecnicas_necessarias')}
