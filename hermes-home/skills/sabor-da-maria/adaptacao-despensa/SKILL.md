---
name: adaptacao-despensa
description: "Monte fichas que cabem na despensa, aproveitando derivados, substituições culinárias e ressalvas para avançar sem repetir perguntas."
version: 1.0.0
author: Gabriel Sousa
license: MIT
metadata:
  hermes:
    tags: [Despensa, Receitas, Adaptação, Dona Maria]
    requires_toolsets: [sabor_da_maria]
    requires_tools: [despensa_listar, despensa_buscar, receita_conferir_ingredientes]
---

# Adaptação à despensa

Use ao montar ou retomar um prato com ingredientes ausentes, medidas abertas ou preparo incompatível. Use o `prato_id` atual e a receita verificada como referência.

1. Consulte a ficha e a despensa. Diferencie o que está disponível para o lote, o que cobre parte dele e o que falta. Use semelhança para localizar candidatos e avalie a função culinária antes de trocar ingredientes.
2. Use derivados simples quando o preparo for viável. Para molho caseiro, registre uma única linha com `nome: "Molho de tomate"`, a medida de molho e `item_despensa: "Tomate"`. A ferramenta calcula o uso de tomate e acrescenta o tempo do molho. Informe em `tempo_preparo_min` apenas o tempo das outras etapas. Em substituições culinárias, registre o insumo usado e `ingrediente_original`.
3. Se um faltante pode ficar de fora, ajuste o preparo. Pizza sem fermento pode ter uma massa fina sem fermentação. Fermento natural exige antecedência e fica como possibilidade para outro lote. Junte as medidas locais estimáveis e explique a base de cada uma. Preserve as medidas que a Dona Maria confirmou.
4. Compare o preparo com a lista do lote antes de salvar. Cada insumo usado nas etapas precisa ter medida na lista, inclusive sal, alho, óleo, azeite e temperos. Registre a quantidade e a base da estimativa dos que usar ou ajuste as etapas para dispensá-los. Chame `receita_conferir_ingredientes` com essa lista, as omissões, o preparo e o tempo. Use o mesmo prato e preserve a fonte e as ressalvas. Se faltar um insumo indispensável, guarde a ficha como rascunho e avance para uma alternativa viável ou ofereça deixá-la para depois.
5. Reúna as confirmações reais da cozinha e apresente a versão para aceite. Use a skill `gestao-estoque-despesas` ao registrar o lote. Inclua no CMV e no histórico as matérias-primas usadas nos derivados. A baixa do disponível e as despesas aparecem quando a pessoa aceita a versão viável.
6. Responda com o que ficou gravado e a próxima decisão que depende da Dona Maria. Se já houver ficha, confirmação ou plano de divulgação salvo, retome esse resultado. Quando ela aceitar a divulgação, prepare e guarde o plano com o preço e a margem disponíveis.
