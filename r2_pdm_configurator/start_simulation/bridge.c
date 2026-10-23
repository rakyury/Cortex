/* Host-only command bridge. Policy is linked from the actual firmware sources.
 * Numbers below describe fictional virtual loads, never commissioned hardware. */
#include "pdm_board_profile.h"
#include "pdm_start_admission.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static PDM_StartAdmission scheduler;
static PDM_StartInputs inputs;
static const PDM_BoardProfile *profile;
static uint32_t now_ms = 100, request_ids[24];
static char last_command[128]="INIT";
static const char *last_result="OK";
static const char *states[] = {"OFF","QUEUED","STARTING","ON","FAULT"};
static const char *reasons[] = {"OK","BAD_CONFIG","BAD_CHANNEL","OLD_REQUEST","EXPIRED",
    "UNHEALTHY","UNKNOWN_OFF","SLOTS","TOTAL_BUDGET","GROUP_BUDGET","COOLDOWN",
    "DEADLINE","FAULT_LATCHED","CANCELLED"};

static void refresh(void)
{
    inputs.sampled_ms = now_ms;
    inputs.total_measured_mA = scheduler.config.base_load_mA;
    for (unsigned i=0;i<profile->channel_count;++i) {
        inputs.channels[i].sampled_ms = now_ms;
        inputs.total_measured_mA += inputs.channels[i].measured_mA;
    }
}
static void step(uint32_t ms)
{
    PDM_StartState before[24];
    for(unsigned i=0;i<profile->channel_count;++i) before[i]=scheduler.channels[i].state;
    now_ms += ms; refresh();
    PDM_Start_Step(&scheduler, &inputs, now_ms);
    /* Virtual plant follows admitted STARTING only. Fault/reset do not
       fabricate OFF proof: current stays until explicit confirm_off. */
    for (unsigned i=0;i<profile->channel_count;++i) {
        if (scheduler.channels[i].state == PDM_START_STARTING && before[i] != PDM_START_STARTING) {
            inputs.channels[i].confirmed_off = false;
            inputs.channels[i].measured_mA = scheduler.config.channels[i].start_mA;
        }
    }
    refresh();
}
static void snapshot(void)
{
    printf("{\"last_command\":\"%s\",\"last_result\":\"%s\",\"profile\":%u,\"count\":%u,\"slots\":%u,\"now_ms\":%lu,"
        "\"simulation_only\":true,\"fixture_only\":true,\"physical_outputs_on\":0,"
        "\"hardware_authorized\":false,\"output_backend\":\"simulated_zero_output_backend\","
        "\"starts_reserved\":%u,\"total_budget_ma\":%lu,\"total_reserved_ma\":%lu,"
        "\"total_measured_ma\":%lu,\"queue\":[",last_command,last_result,profile->id,profile->channel_count,
        scheduler.config.max_starts,(unsigned long)now_ms,scheduler.starts_reserved,
        (unsigned long)scheduler.config.total_budget_mA,(unsigned long)scheduler.total_reserved_mA,
        (unsigned long)inputs.total_measured_mA);
    for(unsigned i=0;i<scheduler.queue_count;++i) printf("%s%u",i?",":"",scheduler.queue[i]+1u);
    printf("],\"groups\":[");
    for(unsigned g=0;g<scheduler.config.group_count;++g)
        printf("%s{\"id\":%u,\"budget_ma\":%lu,\"reserved_ma\":%lu}",g?",":"",g,
            (unsigned long)scheduler.config.group_budget_mA[g],(unsigned long)scheduler.group_reserved_mA[g]);
    printf("],\"channels\":[");
    for(unsigned i=0;i<profile->channel_count;++i) {
        const PDM_StartChannel *c=&scheduler.channels[i];
        const PDM_StartChannelConfig *cfg=&scheduler.config.channels[i];
        printf("%s{\"channel\":%u,\"state\":\"%s\",\"reason\":\"%s\","
            "\"reserve_ma\":%lu,\"measured_ma\":%lu,\"group\":%u,\"priority\":%u,"
            "\"continuous_ma\":%lu,\"start_ma\":%lu,\"slot_reserved\":%s,\"queue_position\":",
            i?",":"",i+1,states[c->state],reasons[c->reason],(unsigned long)c->reserve_mA,
            (unsigned long)inputs.channels[i].measured_mA,cfg->group,cfg->priority,
            (unsigned long)cfg->continuous_mA,(unsigned long)cfg->start_mA,c->slot_reserved?"true":"false");
        unsigned pos=0;
        for(unsigned q=0;q<scheduler.queue_count;++q) if(scheduler.queue[q]==i) pos=q+1;
        if(pos) printf("%u}",pos); else printf("null}");
    }
    puts("]}"); fflush(stdout);
}
static int number(const char *s, uint32_t *out)
{
    if(!*s) return 0;
    uint64_t value=0;
    for(;*s;++s) { if(*s<'0'||*s>'9') return 0; value=value*10u+(unsigned)(*s-'0'); if(value>UINT32_MAX) return 0; }
    *out=(uint32_t)value; return 1;
}
int main(int argc,char **argv)
{
    uint32_t id,slots,total,group;
    if(argc!=5 || !number(argv[1],&id)||!number(argv[2],&slots)||!number(argv[3],&total)||!number(argv[4],&group)) return 2;
    if((id!=14 && id!=24)||slots<2||slots>4||total<20000||total>1000000||group<10000||group>1000000) return 2;
    profile=PDM_BoardProfile_Get((uint16_t)id);
    if(!PDM_BoardProfile_Validate(profile,false)) return 2;
    PDM_StartConfig config={.channel_count=profile->channel_count,.group_count=2,.max_starts=(uint8_t)slots,
        .total_budget_mA=total,.base_load_mA=20000,.request_ttl_ms=60000,.feedback_ttl_ms=1000};
    for(unsigned g=0;g<2;++g) { config.group_budget_mA[g]=group; config.group_base_mA[g]=10000; inputs.group_healthy[g]=true; }
    for(unsigned i=0;i<profile->channel_count;++i) {
        config.channels[i]=(PDM_StartChannelConfig){.continuous_mA=10000,.start_mA=30000,
            .start_duration_ms=1000,.cooldown_ms=100,.group=(uint8_t)(i%2),.priority=0,.commissioned=true};
        if(id==24 && i>=10) { config.channels[i].continuous_mA=17000; config.channels[i].start_mA=20000; }
        inputs.channels[i]=(PDM_StartFeedback){.valid=true,.healthy=true,.confirmed_off=true};
    }
    inputs.valid=inputs.healthy=inputs.link_healthy=true;
    if(!PDM_Start_Init(&scheduler,&config)) return 2;
    refresh(); snapshot();
    char line[128];
    while(fgets(line,sizeof(line),stdin)) {
        char *cmd=strtok(line," \r\n"), *arg=strtok(NULL," \r\n"), *extra=strtok(NULL," \r\n");
        uint32_t n=0;
        if(!cmd || extra || (arg && !number(arg,&n))) return 3;
        if(strcmp(cmd,"SNAPSHOT")==0 && !arg) { snapshot(); continue; }
        strcpy(last_command,cmd); last_result="OK";
        if(strcmp(cmd,"STEP")==0 && arg && n>=1 && n<=60000) step(n);
        else if(strcmp(cmd,"REQUEST_ALL")==0 && !arg) {
            ++now_ms; refresh();
            for(unsigned i=0;i<profile->channel_count;++i) {
                PDM_StartReason result=PDM_Start_Request(&scheduler,(uint8_t)i,++request_ids[i],now_ms,now_ms);
                if(result!=PDM_START_OK) last_result="PARTIAL_OR_REJECTED";
            }
        }
        else if(strcmp(cmd,"RESET")==0 && !arg) { ++now_ms; PDM_Start_Reset(&scheduler,now_ms); refresh(); }
        else if(arg && n>=1 && n<=profile->channel_count) {
            const unsigned i=n-1; PDM_StartFeedback *f=&inputs.channels[i];
            ++now_ms; refresh();
            if(strcmp(cmd,"REQUEST")==0) last_result=reasons[PDM_Start_Request(&scheduler,(uint8_t)i,++request_ids[i],now_ms,now_ms)];
            else if(strcmp(cmd,"CANCEL")==0) PDM_Start_Cancel(&scheduler,(uint8_t)i,now_ms);
            else if(strcmp(cmd,"COMPLETE")==0) {
                if(scheduler.channels[i].state!=PDM_START_STARTING) last_result="INVALID_STATE";
                else {
                    f->start_complete=true; f->confirmed_off=false; f->measured_mA=config.channels[i].continuous_mA;
                    step(1);
                }
            }
            else if(strcmp(cmd,"FAULT")==0) { f->healthy=false; step(1); }
            else if(strcmp(cmd,"CONFIRM_OFF")==0) {
                f->healthy=true; f->confirmed_off=true; f->start_complete=false; f->measured_mA=0;
                step(1);
            }
            else if(strcmp(cmd,"CLEAR_FAULT")==0) last_result=PDM_Start_ClearFault(&scheduler,(uint8_t)i,&inputs,now_ms)?"OK":"REJECTED";
            else return 3;
        } else return 3;
        snapshot();
    }
    return 0;
}
